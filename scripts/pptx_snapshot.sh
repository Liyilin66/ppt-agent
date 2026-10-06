#!/bin/zsh
# Export PPTX files through Microsoft PowerPoint (the real renderer) to PDF + PNG.
# Usage: scripts/pptx_snapshot.sh out_dir file1.pptx [file2.pptx ...]
set -e
out=$1; shift
mkdir -p "$out"
box="$HOME/Library/Containers/com.microsoft.Powerpoint/Data/Documents"
mkdir -p "$box"
for f in "$@"; do
  name=$(basename "$f" .pptx)
  abs=$(cd "$(dirname "$f")" && pwd)/$(basename "$f")
  open -g -a "Microsoft PowerPoint" "$abs"
  sleep 4
  osascript -e "tell application \"Microsoft PowerPoint\" to save active presentation in (POSIX file \"$box/$name.pdf\") as save as PDF" \
            -e 'tell application "Microsoft PowerPoint" to close active presentation saving no'
  sleep 1
  cp "$box/$name.pdf" "$out/$name.pdf"
done
uvx --from pymupdf python - "$out" "$@" <<'PY'
import sys, pathlib, pymupdf
out = pathlib.Path(sys.argv[1])
for f in sys.argv[2:]:
    name = pathlib.Path(f).stem
    doc = pymupdf.open(out / f"{name}.pdf")
    for i, page in enumerate(doc, 1):
        page.get_pixmap(dpi=110).save(out / f"{name}-{i:02d}.png")
PY
echo done
