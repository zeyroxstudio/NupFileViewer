"""Render the SVG into a multi-size Windows executable icon."""
from pathlib import Path
import struct
import sys

from PySide6.QtCore import QBuffer, QIODevice, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


def build_icon(source, output):
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG: {source}")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    offset = 6 + 16 * len(sizes)
    directory, images = [], []
    for size in sizes:
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise ValueError("Cannot encode icon as PNG")
        data = bytes(buffer.data())
        directory.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset))
        images.append(data)
        offset += len(data)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(struct.pack("<HHH", 0, 1, len(sizes)) + b"".join(directory + images))


if __name__ == "__main__":
    build_icon(Path(sys.argv[1]), Path(sys.argv[2]))
