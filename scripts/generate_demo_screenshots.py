from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEMO_PPTX = REPO_ROOT / "examples" / "demo_ai_agent_pm" / "generated_deck.pptx"
DEFAULT_SCREENSHOT_DIR = REPO_ROOT / "examples" / "demo_ai_agent_pm" / "screenshots"
DEFAULT_PATCHED_DEMO_PPTX = REPO_ROOT / "examples" / "demo_ai_agent_pm" / "patched_deck.pptx"
DEFAULT_PATCH_SCREENSHOT_DIR = REPO_ROOT / "examples" / "demo_ai_agent_pm" / "patches" / "screenshots"


class ScreenshotGenerationError(RuntimeError):
    """Raised when optional screenshot generation cannot complete."""


def _load_pillow() -> tuple[object, object, object, object]:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise ScreenshotGenerationError(
            "Optional image assembly requires Pillow, which is expected from the current local environment. "
            "If it is unavailable, rerun with --skip-contact-sheet and omit patch preview generation."
        ) from exc
    return Image, ImageDraw, ImageFont, ImageFont.load_default()


def _resolve_required_command(name: str) -> str:
    command_path = shutil.which(name)
    if command_path is None:
        raise ScreenshotGenerationError(
            f"Missing required external tool: '{name}'. "
            "Install LibreOffice/soffice and pdftoppm, then rerun this optional demo script."
        )
    return command_path


def _run_command(args: list[str], *, description: str) -> None:
    completed = subprocess.run(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise ScreenshotGenerationError(f"{description} failed: {details}")


def _convert_pptx_to_pngs(
    pptx_path: Path,
    *,
    work_dir: Path,
    dpi: int,
    soffice_command: str,
    pdftoppm_command: str,
) -> list[Path]:
    if not pptx_path.exists():
        raise ScreenshotGenerationError(f"PPTX file not found: {pptx_path}")

    staged_pptx = work_dir / pptx_path.name
    shutil.copy2(pptx_path, staged_pptx)

    _run_command(
        [
            soffice_command,
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(work_dir),
            str(staged_pptx),
        ],
        description=f"Converting {pptx_path.name} to PDF with soffice",
    )

    pdf_path = work_dir / f"{pptx_path.stem}.pdf"
    if not pdf_path.exists():
        raise ScreenshotGenerationError(
            f"LibreOffice reported success but did not create PDF: {pdf_path}"
        )

    slide_prefix = work_dir / "slide"
    _run_command(
        [
            pdftoppm_command,
            "-png",
            "-r",
            str(dpi),
            str(pdf_path),
            str(slide_prefix),
        ],
        description=f"Rasterizing {pdf_path.name} to PNG with pdftoppm",
    )

    slide_paths = sorted(work_dir.glob("slide-*.png"))
    if not slide_paths:
        raise ScreenshotGenerationError(f"No slide PNGs were produced for {pptx_path}")
    return slide_paths


def _copy_slide_images(slide_images: list[Path], output_dir: Path, *, prefix: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Regeneration should replace prior demo exports so README references stay stable.
    for stale_path in output_dir.glob(f"{prefix}_*.png"):
        stale_path.unlink()
    written_paths: list[Path] = []
    for index, slide_image in enumerate(slide_images, start=1):
        target_path = output_dir / f"{prefix}_{index:02d}.png"
        shutil.copy2(slide_image, target_path)
        written_paths.append(target_path)
    return written_paths


def _build_contact_sheet(slide_paths: list[Path], output_path: Path) -> Path:
    Image, ImageDraw, _image_font_module, font = _load_pillow()
    images = [Image.open(path).convert("RGB") for path in slide_paths]
    try:
        thumb_size = (360, 203)
        columns = 2 if len(images) <= 4 else 3
        gap = 24
        label_height = 28
        rows = (len(images) + columns - 1) // columns
        canvas_width = columns * thumb_size[0] + (columns + 1) * gap
        canvas_height = rows * (thumb_size[1] + label_height) + (rows + 1) * gap

        canvas = Image.new("RGB", (canvas_width, canvas_height), color="white")
        draw = ImageDraw.Draw(canvas)

        for idx, image in enumerate(images, start=1):
            thumb = image.copy()
            thumb.thumbnail(thumb_size)
            row = (idx - 1) // columns
            column = (idx - 1) % columns
            x = gap + column * (thumb_size[0] + gap)
            y = gap + row * (thumb_size[1] + label_height + gap)
            canvas.paste(thumb, (x, y))
            draw.text((x, y + thumb_size[1] + 6), f"Slide {idx:02d}", fill="black", font=font)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG", optimize=True)
        return output_path
    finally:
        for image in images:
            image.close()


def _build_before_after_image(before_path: Path, after_path: Path, output_path: Path) -> Path:
    Image, ImageDraw, _image_font_module, font = _load_pillow()
    before_image = Image.open(before_path).convert("RGB")
    after_image = Image.open(after_path).convert("RGB")
    try:
        label_height = 40
        gutter = 24
        canvas = Image.new(
            "RGB",
            (before_image.width + after_image.width + gutter * 3, before_image.height + label_height + gutter * 2),
            color="white",
        )
        draw = ImageDraw.Draw(canvas)

        before_x = gutter
        before_y = gutter + label_height
        after_x = before_x + before_image.width + gutter
        after_y = before_y

        draw.text((before_x, gutter), "Before Patch", fill="black", font=font)
        draw.text((after_x, gutter), "After Patch", fill="black", font=font)
        canvas.paste(before_image, (before_x, before_y))
        canvas.paste(after_image, (after_x, after_y))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output_path, format="PNG", optimize=True)
        return output_path
    finally:
        before_image.close()
        after_image.close()


def generate_demo_screenshots(
    *,
    pptx_path: Path = DEFAULT_DEMO_PPTX,
    output_dir: Path = DEFAULT_SCREENSHOT_DIR,
    dpi: int = 150,
    create_contact_sheet: bool = True,
) -> dict[str, Path | list[Path]]:
    # This optional helper stays outside the core pipeline: it converts an existing demo PPTX artifact
    # into README-friendly PNG previews and must not become a runtime dependency for generation.
    soffice_command = _resolve_required_command("soffice")
    pdftoppm_command = _resolve_required_command("pdftoppm")

    with tempfile.TemporaryDirectory(prefix="ppt-agent-screenshots-") as tmpdir_name:
        slide_images = _convert_pptx_to_pngs(
            pptx_path,
            work_dir=Path(tmpdir_name),
            dpi=dpi,
            soffice_command=soffice_command,
            pdftoppm_command=pdftoppm_command,
        )
        written_slides = _copy_slide_images(slide_images, output_dir, prefix="slide")

    result: dict[str, Path | list[Path]] = {
        "pptx_path": pptx_path,
        "output_dir": output_dir,
        "slides": written_slides,
    }
    if create_contact_sheet:
        result["contact_sheet"] = _build_contact_sheet(written_slides, output_dir / "contact_sheet.png")
    return result


def generate_patch_demo_screenshots(
    *,
    base_slide_path: Path,
    patched_pptx_path: Path = DEFAULT_PATCHED_DEMO_PPTX,
    output_dir: Path = DEFAULT_PATCH_SCREENSHOT_DIR,
    patched_slide_index: int = 1,
    dpi: int = 150,
) -> dict[str, Path]:
    # Patch previews are generated from already-rendered PPTX outputs so the patch flow remains
    # inspectable in docs without changing the structured patch or renderer contracts.
    soffice_command = _resolve_required_command("soffice")
    pdftoppm_command = _resolve_required_command("pdftoppm")

    with tempfile.TemporaryDirectory(prefix="ppt-agent-patch-screenshots-") as tmpdir_name:
        patched_slide_images = _convert_pptx_to_pngs(
            patched_pptx_path,
            work_dir=Path(tmpdir_name),
            dpi=dpi,
            soffice_command=soffice_command,
            pdftoppm_command=pdftoppm_command,
        )
        if patched_slide_index < 1 or patched_slide_index > len(patched_slide_images):
            raise ScreenshotGenerationError(
                f"Requested patched slide index {patched_slide_index} is out of range for {patched_pptx_path}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        for stale_path in output_dir.glob("*.png"):
            stale_path.unlink()
        patched_slide_path = output_dir / f"patched_slide_{patched_slide_index:02d}.png"
        shutil.copy2(patched_slide_images[patched_slide_index - 1], patched_slide_path)

    before_after_path = _build_before_after_image(
        base_slide_path,
        patched_slide_path,
        output_dir / "patch_before_after.png",
    )
    return {
        "patched_slide": patched_slide_path,
        "before_after": before_after_path,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate optional demo screenshots from the official ppt-agent PPTX artifacts."
    )
    parser.add_argument("--pptx", type=Path, default=DEFAULT_DEMO_PPTX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument(
        "--skip-contact-sheet",
        action="store_true",
        help="Do not create contact_sheet.png.",
    )
    parser.add_argument(
        "--include-patch-demo",
        action="store_true",
        help="Also export the patched cover slide and a before/after comparison sheet.",
    )
    parser.add_argument("--patched-pptx", type=Path, default=DEFAULT_PATCHED_DEMO_PPTX)
    parser.add_argument("--patched-output-dir", type=Path, default=DEFAULT_PATCH_SCREENSHOT_DIR)
    parser.add_argument("--patched-slide-index", type=int, default=1)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        result = generate_demo_screenshots(
            pptx_path=args.pptx,
            output_dir=args.output_dir,
            dpi=args.dpi,
            create_contact_sheet=not args.skip_contact_sheet,
        )
        slides = result["slides"]
        if not isinstance(slides, list):
            raise ScreenshotGenerationError("Internal error: expected slide list output.")
        for slide_path in slides:
            print(slide_path)
        contact_sheet = result.get("contact_sheet")
        if contact_sheet is not None:
            print(contact_sheet)

        if args.include_patch_demo:
            if args.patched_slide_index < 1 or args.patched_slide_index > len(slides):
                raise ScreenshotGenerationError(
                    f"Requested patched slide index {args.patched_slide_index} is out of range for the base deck"
                )
            patch_result = generate_patch_demo_screenshots(
                base_slide_path=slides[args.patched_slide_index - 1],
                patched_pptx_path=args.patched_pptx,
                output_dir=args.patched_output_dir,
                patched_slide_index=args.patched_slide_index,
                dpi=args.dpi,
            )
            print(patch_result["patched_slide"])
            print(patch_result["before_after"])
    except ScreenshotGenerationError as exc:
        print(f"Screenshot generation skipped: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
