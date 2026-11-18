"""Conservative numeric grounding checks, without model calls.

Local keyword association is a rule-based guard, not a semantic entailment proof.
Source/reference text and speaker notes are not visible numeric claims.
"""
from __future__ import annotations

from decimal import Decimal
import re

from pydantic import TypeAdapter, ValidationError

from .visual.content import ArchetypeContent, StatementContent

# Thousands may be grouped with commas or, in English reports (IEA), spaces: "27 000", "1 000 000".
_NUMBER = re.compile(r'(?<![\d.])[-+]?\d+(?:,\d{3}(?!\d))*(?:[ \u00a0\u202f]\d{3}(?![\d,.]))*(?:\.\d+)?\s*'
                     r'(?:[%％]|万亿|亿|万|千|百|pp|percentage points?|trillion|billion|million|thousand|bn\b|mn\b)?', re.I)
# A figure is compared by magnitude, so "2,000万" (Chinese page) is the "20 million" of an English source.
_SCALE = {'万亿': Decimal(10) ** 12, '亿': Decimal(10) ** 8, '万': Decimal(10) ** 4, '千': Decimal(1000), '百': Decimal(100),
          'trillion': Decimal(10) ** 12, 'billion': Decimal(10) ** 9, 'bn': Decimal(10) ** 9, 'million': Decimal(10) ** 6,
          'mn': Decimal(10) ** 6, 'thousand': Decimal(1000)}
# Roadmap phases and bar positions are the deck's advice, not claims about a source.
_SKIP = {'source', 'speaker_notes', 'ref', 'archetype', 'unit_format', 'chart', 'phases', 'start', 'end',
         'series', 'figure', 'diagram', 'visual'}  # checked by v2.data_series and v2.figures, more strictly than here
_ADAPTER = TypeAdapter(ArchetypeContent)


def _key(value: str) -> tuple[Decimal, str]:
    match = re.match(r'([-+]?\d+(?:,\d{3}(?!\d))*(?:[ \u00a0\u202f]\d{3}(?![\d,.]))*(?:\.\d+)?)\s*(.*)', value.strip())
    return Decimal(re.sub(r'[,\s\u202f]', '', match[1])), match[2].lower().replace('％', '%')


def _magnitude(value: str) -> tuple[Decimal, str]:
    """A figure as an amount: "2,000万", "20 million" and "20 000 000" are one key; "%" and points keep theirs."""
    match = re.match(r'([-+]?\d[\d,  \u202f]*(?:\.\d+)?)\s*(.*)', value.strip())
    number = Decimal(re.sub(r'[,\s\u202f]', '', match[1]))
    unit = match[2].lower().replace('％', '%').strip()
    if unit.startswith('percentage point'):
        unit = 'pp'
    if unit in _SCALE:
        return (number * _SCALE[unit]).normalize(), ''
    return number.normalize(), unit


_YEAR = re.compile(r'(?:19|20)\d{2}')


def _cjk_share(text: str) -> float:
    letters = re.findall(r'[\u3400-\u9fffA-Za-z]', text or '')
    return sum(bool(re.match(r'[\u3400-\u9fff]', c)) for c in letters) / len(letters) if letters else 0.0
_COUNTED = re.compile(r'[ \t]*(?:[个家项款台人吨元种位名件次倍兆]|多个|多家|多项|余|以上|的|美元|欧元)')


def _label_tokens(label: str) -> list[str]:
    """Bigrams survive paraphrase and PDF line breaks; digits in names count."""
    tokens = []
    for run in re.findall(r'[\u3400-\u9fff]+', label):
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(word.lower() for word in re.findall(r'[A-Za-z]+', label))
    tokens.extend(m.group().strip().lower() for m in _NUMBER.finditer(label)
                  if not _YEAR.fullmatch(m.group().strip()))
    return tokens


def _covers(segment: str, tokens: list[str], head: str, *, tail: bool, years=()) -> bool:
    if any(year not in segment for year in years):
        return False
    if not tokens:
        return bool(years)  # a time-series category: "2022年" binds by its year
    found = [segment.rfind(token) for token in tokens]
    matched = [(i, token) for i, token in zip(found, tokens) if i >= 0]
    if len(matched) < min(2, len(tokens)) or len(matched) < len(tokens) / 2:
        return False
    end = max(i + len(token) for i, token in matched)
    if head and not any(char in segment for char in head) and \
            len(re.findall(r'[\u3400-\u9fff]', segment[end:])) >= 2:
        # Chinese metric names are head-final: 融资金额 is not 融资事件. A missing
        # head with nothing after the match ("552吨二氧化碳" for 碳排放) is fine.
        return False
    if tail:
        # The label must sit right before the value: "GPT-4 Turbo 128k" does
        # not support "GPT-4 128k", and a far-away mention binds nothing.
        gap = re.sub(r'\d', '', segment[end:])
        if re.search(r'[a-z]', gap) or len(gap) > 16:
            return False
    return True


def _metric_match(text: str, numbers: list, match, label: str) -> bool:
    """A value is supported when the label occupies the text the value owns.

    That text runs back to the previous number (or 40 characters), so in
    "生活助手 30.0% 会议纪要 29.7%" the label 会议纪要 owns only 29.7%. Numbers that
    are part of the label (Llama 2) and years do not end the span.
    """
    tokens = _label_tokens(label)
    years = _YEAR.findall(label)
    if not tokens and not years:
        return False
    own = {_key(m.group()) for m in _NUMBER.finditer(label)}
    runs = re.findall(r'[\u3400-\u9fff]{2,}', label)
    head = runs[-1][-2:] if runs else ''

    def skip(n):
        if _key(n.group()) in own or _YEAR.fullmatch(n.group().strip()):
            return True
        # Footnote markers ("21.5%1。", "遭到“劫持”12。") are not values.
        return bool(re.fullmatch(r'\d{1,3}', n.group()) and n.start() and not text[n.start() - 1].isspace()
                    and text[n.end():n.end() + 1] in ('', '\n', '。', '，', '；'))

    lo, prev = max(0, match.start() - 40), None
    for n in reversed(numbers):
        if lo < n.end() <= match.start() and not skip(n):
            lo, prev = n.end(), n
            break
    before = re.sub(r'\s+', '', text[lo:match.start()]).lower()
    opens_line = not re.search(r'[\u3400-\u9fffA-Za-z]', text[text.rfind('\n', 0, match.start()) + 1:match.start()])
    labelled_before = bool(re.search(r'[\u3400-\u9fffa-z]', before))
    if labelled_before and _covers(before, tokens, head, tail=True, years=years):
        return True
    # Chinese prose often puts the value first ("78% 的企业应用了…", "552吨二氧化碳").
    # The value owns the following words up to the end of its clause; text that
    # runs into the next value belongs to that value instead, except on rows
    # that open with the value ("30.0% 生活助手").
    # A measure word right after the value ("1919个深度合成算法", "1.1万多个目标")
    # makes the following noun the thing counted, up to the next list item.
    counted = bool(_COUNTED.match(text, match.end()))
    hi, closed = min(len(text), match.end() + 40), False
    for i in range(match.end(), hi):
        if text[i] in '。，；！？,;' or (counted and text[i] == '、'):
            hi, closed = i, True
            break
    for n in numbers:
        if match.end() <= n.start() < hi and not skip(n):
            hi, closed = n.start(), counted
            break
    if counted:
        after = re.sub(r'\s+', '', text[match.end():hi]).lower()
        if _covers(before + '|' + after, tokens, head, tail=False, years=years):
            return True
    elif closed or (opens_line and not labelled_before):
        if _covers(re.sub(r'\s+', '', text[match.end():hi]).lower(), tokens, head, tail=False, years=years):
            return True
    # One subject, several values: "融资占比从2022年的4.5%上升至2024年上半年的12.1%",
    # "规模达6233亿美元，同比增长21.5%". Only a continuation shares the subject; a list does not.
    if prev is not None and re.search(r'[至到]|同比|环比|较上', text[prev.end():match.start()]) \
            and not re.search(r'[A-Za-z]', text[prev.end():match.start()]):
        start = max(text.rfind(mark, 0, prev.start()) for mark in '。；')  # PDF lines wrap mid-sentence
        clause = re.sub(r'\s+', '', text[max(start + 1, prev.start() - 60):match.start()]).lower()
        return _covers(clause, tokens, head, tail=False, years=())
    return False


_DECREASE = re.compile(r'减少|下降|降低|缩短|减轻|降幅|下滑|减重|减')


def number_contexts(issues: list[dict], evidence, width: int = 50, limit: int = 2) -> list[str]:
    """Source sentences around each rejected value, so a retry can reuse the wording."""
    text = evidence if isinstance(evidence, str) else getattr(evidence, 'text', '')
    text = re.sub(r'^\[[^\n]*\]\s*$', '', text, flags=re.M)
    result = []
    for issue in issues:
        try:
            wanted = _magnitude(issue['value'].lstrip('-+'))
        except (TypeError, ValueError, ArithmeticError):
            continue
        hits = [m for m in _NUMBER.finditer(text) if _magnitude(m.group()) == wanted][:limit]
        for m in hits:
            snippet = re.sub(r'\s+', '', text[max(0, m.start() - width):m.end() + width // 2])
            result.append(f"{issue['value']}: …{snippet}…")
    return result


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
                bound=(data.get('categories') or [])[i] if path=='values' and i<len((data.get('categories') or [])) else label
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
        indexed.setdefault(_magnitude(match.group()),[]).append(match)
    issues=[]
    for path,value,label in _fields(content.model_dump(mode='json')):
        if re.fullmatch(r'milestones\.\d+\.date',path) and re.fullmatch(r'\s*(?:第|阶段|step|phase)?\s*\d{1,2}\s*(?:步|阶段)?\s*',value,re.I):
            continue  # "01", "第2阶段": an ordinal step, not a statistic
        # "6233" labelled "产业规模（亿美元）": the unit sits in the label.
        unit=re.search(r'[（(]\s*(万亿|亿|万|[%％])',label)
        # "7×24小时" is one expression, not a 7 and a 24.
        product=re.search(r'\d+\s*[×xX*]\s*\d+',value)
        if product and re.sub(r'\s','',product[0]) in re.sub(r'\s','',text):
            value=value.replace(product[0],'')
        for match in _NUMBER.finditer(value):
            # "5维筛选", "3个阶段": the page counting its own points, not a statistic.
            # Removing it would also leave a broken title.
            if not label and re.fullmatch(r'\d{1,2}',match.group().strip()) and int(match.group())<=12:
                continue
            candidates=indexed.get(_magnitude(match.group()),[])
            if not candidates and unit and not _key(match.group())[1]:
                candidates=indexed.get(_magnitude(match.group()+unit[1]),[])
            if not candidates and match.group().strip().startswith('-'):
                # "-11%" for "运动次数减少 11%": a decline written as a negative value.
                candidates=[c for c in indexed.get(_magnitude(match.group().strip()[1:]),[])
                            if _DECREASE.search(text[max(0,c.start()-8):c.start()]+label)]
            reason = 'number_missing' if not candidates else None
            # A Chinese label cannot be found word for word in an English source: the
            # figure is bound by its magnitude only there, and what it measures is left to the claim audit.
            # (Latin product names in a Chinese source, "Llama 2", are not another language.)
            other_language = _cjk_share(label) > 0.3 and _cjk_share(text) < 0.05
            if candidates and label and not other_language and not any(_metric_match(text,numbers,c,label) for c in candidates):
                reason='metric_mismatch'
            if reason:
                issues.append({'path':path,'value':match.group().strip(),'label':label,'reason':reason})
    return issues


def _readable_fragments(data: dict) -> list[str]:
    """Surviving body copy as phrases a reader can follow, not raw field values."""
    def pair(label, value, unit=''):
        return f"{label} {value}{unit}".strip()
    unit='%' if '%' in (data.get('unit_format') or '') else ''
    result=[]
    result += [pair(m['label'],m['value']) for m in data.get('metrics') or []]
    result += [pair(c,f"{v:g}",unit) for c,v in zip(data.get('categories') or [],data.get('values') or [])]
    result += [i['text'] for i in data.get('insights') or []]
    result += [f"{i['heading']}：{i['body']}" for i in data.get('items') or []]
    result += [f"{i['label']}：{i['body']}" for i in (data.get('steps') or [])]
    result += [pair(m['date'],m['label']) for m in (data.get('milestones') or [])]
    for row in (data.get('rows') or []):
        if isinstance(row,list):
            result.append(' '.join(row))
        else:
            result.append(row.get('headline') or f"{row['label']}：{row['body']}")
    for side in ('left','right'):
        result += (data.get(side) or {}).get('points',[])
    return [text for text in dict.fromkeys(result) if text]


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
        elif path[0]=='highlight' and data.get('archetype')=='rich':
            data['highlight']=None  # a focal figure that cannot be shown is no focal fact
        elif path[0] in {'metrics','items','steps','milestones','insights','rows'} and len(path)>2 and path[-1]!='note':
            remove.add((path[0],int(path[1])))
        else:
            target=data
            for part in path[:-1]:
                target=target[int(part)] if isinstance(target,list) else target[part]
            key=int(path[-1]) if isinstance(target,list) else path[-1]
            if key in ('lead','takeaway','kicker','support','note'):
                target[key]=None  # optional copy goes whole; "预计达美元，同比增长" reads broken
            elif isinstance(target[key],str):
                bad=_key(issue['value'])
                target[key]=_NUMBER.sub(lambda m:'' if _key(m.group())==bad else m.group(),target[key]).strip() or '定性信息'
        item_removed = len(path)>1 and path[1].isdigit() and (path[0],int(path[1])) in remove
        records.append({**issue,'action':'removed_item' if item_removed else 'removed_number'})
    for field,index in sorted(remove,key=lambda item:(item[0],-item[1])):
        del data[field][index]
        if field=='values':
            del data['categories'][index]
    try:
        if data.get('archetype')=='rich':
            from .visual.content import RichContent
            return RichContent.model_validate(data),records
        return _ADAPTER.validate_python(data),records
    except ValidationError:
        # When the archetype minimum cardinality fails, carry surviving copy in
        # a qualitative statement; all surviving visible text remains checked.
        fragments=_readable_fragments(data)
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
