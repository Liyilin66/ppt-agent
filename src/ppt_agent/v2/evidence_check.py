"""Conservative numeric grounding checks, without model calls.

Local keyword association is a rule-based guard, not a semantic entailment proof.
Source/reference text and speaker notes are not visible numeric claims.
"""
from __future__ import annotations

from decimal import Decimal
import re

from pydantic import TypeAdapter, ValidationError

from .visual.content import ArchetypeContent, StatementContent

_NUMBER = re.compile(r'(?<![\d.])[-+]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:[%％]|万亿|亿|万|千|百|pp)?', re.I)
_SKIP = {'source', 'speaker_notes', 'ref', 'archetype', 'unit_format', 'chart'}
_ADAPTER = TypeAdapter(ArchetypeContent)


def _key(value: str) -> tuple[Decimal, str]:
    match = re.match(r'([-+]?\d[\d,]*(?:\.\d+)?)\s*(.*)', value.strip())
    return Decimal(match[1].replace(',', '')), match[2].lower().replace('％', '%')


_YEAR = re.compile(r'(?:19|20)\d{2}')


def _label_tokens(label: str) -> list[str]:
    """Bigrams survive paraphrase and PDF line breaks; digits in names count."""
    tokens = []
    for run in re.findall(r'[\u3400-\u9fff]+', label):
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(word.lower() for word in re.findall(r'[A-Za-z]+', label))
    tokens.extend(m.group().strip().lower() for m in _NUMBER.finditer(label)
                  if not _YEAR.fullmatch(m.group().strip()))
    return tokens


def _covers(segment: str, tokens: list[str], head: str, *, tail: bool) -> bool:
    found = [segment.rfind(token) for token in tokens]
    matched = [(i, token) for i, token in zip(found, tokens) if i >= 0]
    if len(matched) < min(2, len(tokens)) or len(matched) < len(tokens) / 2:
        return False
    if head and not any(char in segment for char in head):
        # Chinese metric names are head-final: 融资金额 is not 融资事件.
        return False
    if tail:
        # The label must sit right before the value: "GPT-4 Turbo 128k" does
        # not support "GPT-4 128k", and a far-away mention binds nothing.
        gap = segment[max(i + len(token) for i, token in matched):]
        if re.search(r'[a-z]', gap) or len(gap) > 12:
            return False
    return True


def _metric_match(text: str, numbers: list, match, label: str) -> bool:
    """A value is supported when the label occupies the text the value owns.

    That text runs back to the previous number (or 40 characters), so in
    "生活助手 30.0% 会议纪要 29.7%" the label 会议纪要 owns only 29.7%. Numbers that
    are part of the label (Llama 2) and years do not end the span.
    """
    tokens = _label_tokens(label)
    if not tokens:
        return False
    own = {_key(m.group()) for m in _NUMBER.finditer(label)}
    runs = re.findall(r'[\u3400-\u9fff]{2,}', label)
    head = runs[-1][-2:] if runs else ''

    def skip(n):
        return _key(n.group()) in own or _YEAR.fullmatch(n.group().strip())

    lo = max(0, match.start() - 40)
    for n in reversed(numbers):
        if lo < n.end() <= match.start() and not skip(n):
            lo = n.end()
            break
    before = re.sub(r'\s+', '', text[lo:match.start()]).lower()
    if re.search(r'[\u3400-\u9fffa-z]', before):
        return _covers(before, tokens, head, tail=True)
    # Value-first rows ("30.0% 生活助手") own the text up to the next number,
    # but only when the value opens its line.
    if re.search(r'[\u3400-\u9fffA-Za-z]', text[text.rfind('\n', 0, match.start()) + 1:match.start()]):
        return False
    hi = min(len(text), match.end() + 40)
    for n in numbers:
        if match.end() <= n.start() < hi and not skip(n):
            hi = n.start()
            break
    return _covers(re.sub(r'\s+', '', text[match.end():hi]).lower(), tokens, head, tail=False)


def _fields(data: dict):
    def walk(value, path='', label=''):
        if isinstance(value, dict):
            bound=value.get('label','') if 'value' in value else ''
            for key, child in value.items():
                if key in _SKIP:
                    continue
                yield from walk(child, f'{path}.{key}'.strip('.'), bound if key=='value' else '')
        elif isinstance(value,list):
            for i,child in enumerate(value):
                bound=data.get('categories',[])[i] if path=='values' and i<len(data.get('categories',[])) else label
                yield from walk(child,f'{path}.{i}',bound)
        elif isinstance(value,(str,float,int)):
            text=str(value)
            if path.startswith('values.') and '%' in (data.get('unit_format') or ''):
                text += '%'
            yield path,text,label
    yield from walk(data)


def _cited_text(content, packet) -> str:
    source=content.source or ''
    if not source.strip():
        return ''
    headers=list(re.finditer(r'^\[([^\n]+?)\s*\|\s*([^\n]+?)\]\s*\n', packet.text, re.M))
    selected=[]
    for i,header in enumerate(headers):
        name,location=header[1].strip(),header[2].strip()
        clauses=[clause.strip() for clause in re.split(r'[;；]',source)]
        keep=False
        for clause in clauses:
            if name.startswith(('https://','http://')):
                keep=name in re.findall(r'https?://[^\s；;，,）)]+',clause)
            elif name in clause or (len(packet.references)==1 and packet.references[0]==name):
                normalized=re.sub(r'\bp(?:ages?)?\.?\s*(\d+)(?:[-–—](\d+))?',lambda m:'第'+m[1]+('-'+m[2] if m[2] else '')+'页',clause,flags=re.I)
                expression=r'(?:第\s*)?(\d+(?:\s*[-–—]\s*\d+)?(?:\s*[,，、/]\s*\d+(?:\s*[-–—]\s*\d+)?)*)\s*页'
                page=re.search(r'\bp(\d+)',location,re.I)
                for group in re.findall(expression,normalized):
                    for span in re.split(r'[,，、/]',group):
                        ns=[int(n) for n in re.findall(r'\d+',span)]
                        if page and ns[0]<=int(page[1])<=ns[-1]:
                            keep=True
                if not page and name in clause:
                    keep=True
            if keep:
                break
        if keep:
            selected.append(packet.text[header.end():headers[i+1].start() if i+1<len(headers) else len(packet.text)])
    return '\n'.join(selected)


def check_content_numbers(content, evidence) -> list[dict]:
    """Check visible numbers against already citation-scoped evidence text."""
    text = evidence if isinstance(evidence,str) else _cited_text(content,evidence)
    # Do not count physical page numbers and URLs in packet headers as evidence.
    text = re.sub(r'^\[[^\n]*\]\s*$', '', text, flags=re.M)
    numbers=list(_NUMBER.finditer(text))
    indexed={}
    for match in numbers:
        indexed.setdefault(_key(match.group()),[]).append(match)
    issues=[]
    for path,value,label in _fields(content.model_dump(mode='json')):
        for match in _NUMBER.finditer(value):
            candidates=indexed.get(_key(match.group()),[])
            reason = 'number_missing' if not candidates else None
            if candidates and label and not any(_metric_match(text,numbers,c,label) for c in candidates):
                reason='metric_mismatch'
            if reason:
                issues.append({'path':path,'value':match.group().strip(),'label':label,'reason':reason})
    return issues


def sanitize_content_numbers(content, issues: list[dict]):
    """Remove bad claims, preserve valid neighbours, and return valid content."""
    data=content.model_dump(mode='json')
    remove=set()
    records=[]
    for issue in issues:
        path=issue['path'].split('.')
        # A numeric chart value and its category form one inseparable item.
        if path[0]=='values':
            remove.add(('values',int(path[1])))
        elif path[0] in {'metrics','items','steps','milestones','insights'} and len(path)>2:
            remove.add((path[0],int(path[1])))
        else:
            target=data
            for part in path[:-1]:
                target=target[int(part)] if isinstance(target,list) else target[part]
            key=int(path[-1]) if isinstance(target,list) else path[-1]
            if isinstance(target[key],str):
                bad=_key(issue['value'])
                target[key]=_NUMBER.sub(lambda m:'' if _key(m.group())==bad else m.group(),target[key]).strip() or '定性信息'
        item_removed = len(path)>1 and path[1].isdigit() and (path[0],int(path[1])) in remove
        records.append({**issue,'action':'removed_item' if item_removed else 'removed_number'})
    for field,index in sorted(remove,key=lambda item:(item[0],-item[1])):
        del data[field][index]
        if field=='values':
            del data['categories'][index]
    try:
        return _ADAPTER.validate_python(data),records
    except ValidationError:
        # When the archetype minimum cardinality fails, carry surviving copy in
        # a qualitative statement; all surviving visible text remains checked.
        fragments=[]
        for path,text,_ in _fields(data):
            if path not in {'title','kicker','lead','takeaway'}:
                fragments.append(text)
        base={k:data[k] for k in ('title','kicker','lead','takeaway','source','speaker_notes')}
        kept=[]
        for fragment in fragments:
            if len('；'.join([*kept,fragment])) <=100:
                kept.append(fragment)
        # Promote the page's own message; never put instructions on a slide.
        for field in ('takeaway','lead'):
            if base[field] and len(base[field])<=44:
                statement=base.pop(field)
                break
        else:
            statement=base['title']
        clean=StatementContent(**base,statement=statement,support='；'.join(kept) or None)
        return clean,records
