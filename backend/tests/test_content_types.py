"""Media is identified from its bytes, not from what the caller claimed."""

from __future__ import annotations

import struct
import zlib

import pytest

from app.domain.content_types import (
    GIF,
    IMAGE_FORMATS,
    JPEG,
    MAX_IMAGE_PIXELS,
    MP4,
    PDF,
    PNG,
    WAV,
    WEBP,
    MediaTooLargeError,
    MediaType,
    UnsupportedMediaError,
    sniff,
    verify_declared,
)


def png_bytes(width: int, height: int) -> bytes:
    """A PNG header describing the given dimensions."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return b"\x89PNG\r\n\x1a\n" + chunk


def jpeg_bytes(width: int, height: int) -> bytes:
    """A JPEG with an APP0 segment and a start-of-frame stating the size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof = (
        b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", height, width) + b"\x01"
    )
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def webp_bytes(width: int, height: int) -> bytes:
    """A lossy WebP header describing the given dimensions."""
    vp8 = b"VP8 " + struct.pack("<I", 10) + b"\x00" * 6
    vp8 += struct.pack("<HH", width & 0x3FFF, height & 0x3FFF)
    return b"RIFF" + struct.pack("<I", len(vp8) + 4) + b"WEBP" + vp8


class TestFormatDetection:
    def test_jpeg_is_recognised_from_its_magic_bytes(self) -> None:
        assert sniff(jpeg_bytes(64, 48)).format is JPEG

    def test_png_is_recognised_from_its_magic_bytes(self) -> None:
        assert sniff(png_bytes(10, 10)).format is PNG

    def test_webp_is_distinguished_from_other_riff_containers(self) -> None:
        assert sniff(webp_bytes(20, 30)).format is WEBP

    def test_wav_shares_the_riff_header_but_is_not_webp(self) -> None:
        wav = b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt "
        result = sniff(wav)
        assert result.format is WAV
        assert result.format.media_type is MediaType.AUDIO

    def test_gif_is_recognised(self) -> None:
        assert sniff(b"GIF89a" + struct.pack("<HH", 4, 5) + b"\x00").format is GIF

    def test_pdf_is_a_document_not_an_image(self) -> None:
        result = sniff(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        assert result.format is PDF
        assert result.format.media_type is MediaType.DOCUMENT

    def test_mp4_is_recognised_by_its_ftyp_box(self) -> None:
        mp4 = b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2"
        assert sniff(mp4).format is MP4

    def test_unrecognised_bytes_are_refused(self) -> None:
        with pytest.raises(UnsupportedMediaError, match="not a recognised"):
            sniff(b"this is just some text, not media at all")

    def test_empty_media_is_refused(self) -> None:
        with pytest.raises(UnsupportedMediaError, match="empty"):
            sniff(b"")


class TestDeclaredTypeIsNotTrusted:
    """The claim and the content are checked against each other.

    A file that says it is a JPEG and is really a PDF is the interesting case:
    the old allowlist accepted it because the *claim* was allowed.
    """

    def test_a_pdf_declared_as_jpeg_is_refused(self) -> None:
        with pytest.raises(UnsupportedMediaError, match="not accepted here"):
            sniff(b"%PDF-1.7\n", allowed=IMAGE_FORMATS)

    def test_a_declared_type_contradicting_the_content_is_refused(self) -> None:
        detected = sniff(png_bytes(8, 8)).format
        with pytest.raises(UnsupportedMediaError, match="declares image/jpeg"):
            verify_declared("image/jpeg", detected)

    def test_a_matching_declaration_is_accepted(self) -> None:
        verify_declared("image/png", sniff(png_bytes(8, 8)).format)

    def test_a_declaration_with_parameters_is_accepted(self) -> None:
        verify_declared("image/png; charset=binary", sniff(png_bytes(8, 8)).format)

    def test_declaring_nothing_is_allowed_because_bytes_decide(self) -> None:
        verify_declared(None, sniff(png_bytes(8, 8)).format)
        verify_declared("application/octet-stream", sniff(png_bytes(8, 8)).format)


class TestDimensionsAndBombs:
    def test_png_dimensions_are_read_from_the_header(self) -> None:
        dimensions = sniff(png_bytes(640, 480)).dimensions
        assert dimensions is not None
        assert (dimensions.width, dimensions.height) == (640, 480)

    def test_jpeg_dimensions_are_read_by_walking_segments(self) -> None:
        dimensions = sniff(jpeg_bytes(1920, 1080)).dimensions
        assert dimensions is not None
        assert (dimensions.width, dimensions.height) == (1920, 1080)

    def test_webp_dimensions_are_read_from_the_header(self) -> None:
        dimensions = sniff(webp_bytes(300, 200)).dimensions
        assert dimensions is not None
        assert (dimensions.width, dimensions.height) == (300, 200)

    def test_a_tiny_file_claiming_enormous_dimensions_is_refused(self) -> None:
        """A decompression bomb is small on disk and ruinous in memory."""
        bomb = png_bytes(60_000, 60_000)
        assert len(bomb) < 100
        with pytest.raises(MediaTooLargeError, match="pixel ceiling"):
            sniff(bomb)

    def test_an_image_at_the_ceiling_is_still_accepted(self) -> None:
        side = int(MAX_IMAGE_PIXELS**0.5)
        assert sniff(png_bytes(side, side)).format is PNG
