"""Identify media from its bytes, never from what the caller claimed.

A browser's ``Content-Type`` and a filename's extension are both attacker
controlled. Enrolment previously accepted an upload because its declared type
was in an allowlist, which means the allowlist described the claim rather than
the file. Everything here reads the actual bytes.

Two separate protections live here:

* **Identification.** Magic bytes decide the type. A declared type that
  disagrees with the content is a rejection, not a correction, because the
  disagreement itself is the signal worth acting on.
* **Bomb resistance.** Image dimensions are read from the header, so a 40 KB
  file declaring 60000x60000 pixels is refused before any decoder is asked to
  allocate for it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import StrEnum


class MediaType(StrEnum):
    """The modality of a media asset, used to route processing."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


class UnsupportedMediaError(Exception):
    """The bytes are not a media type this deployment accepts."""


class MediaTooLargeError(Exception):
    """The media exceeds a configured ceiling."""


@dataclass(frozen=True, slots=True)
class MediaFormat:
    """A recognised media format."""

    mime_type: str
    media_type: MediaType
    extension: str


@dataclass(frozen=True, slots=True)
class Dimensions:
    """Pixel dimensions read from an image header."""

    width: int
    height: int

    @property
    def pixels(self) -> int:
        """Total pixel count, which is what decoding actually costs."""
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class SniffResult:
    """What the bytes turned out to be."""

    format: MediaFormat
    #: None for formats whose dimensions we do not parse, and for non-images.
    dimensions: Dimensions | None = None


JPEG = MediaFormat("image/jpeg", MediaType.IMAGE, "jpg")
PNG = MediaFormat("image/png", MediaType.IMAGE, "png")
WEBP = MediaFormat("image/webp", MediaType.IMAGE, "webp")
GIF = MediaFormat("image/gif", MediaType.IMAGE, "gif")
TIFF = MediaFormat("image/tiff", MediaType.IMAGE, "tiff")
BMP = MediaFormat("image/bmp", MediaType.IMAGE, "bmp")

PDF = MediaFormat("application/pdf", MediaType.DOCUMENT, "pdf")

MP4 = MediaFormat("video/mp4", MediaType.VIDEO, "mp4")
WEBM = MediaFormat("video/webm", MediaType.VIDEO, "webm")
MATROSKA = MediaFormat("video/x-matroska", MediaType.VIDEO, "mkv")
QUICKTIME = MediaFormat("video/quicktime", MediaType.VIDEO, "mov")
AVI = MediaFormat("video/x-msvideo", MediaType.VIDEO, "avi")

WAV = MediaFormat("audio/wav", MediaType.AUDIO, "wav")
MP3 = MediaFormat("audio/mpeg", MediaType.AUDIO, "mp3")
FLAC = MediaFormat("audio/flac", MediaType.AUDIO, "flac")
OGG = MediaFormat("audio/ogg", MediaType.AUDIO, "ogg")
M4A = MediaFormat("audio/mp4", MediaType.AUDIO, "m4a")

#: Formats the face pipeline has always accepted. Kept as a named set so the
#: existing endpoints can keep their narrower contract while media ingestion
#: accepts more.
IMAGE_FORMATS: frozenset[MediaFormat] = frozenset({JPEG, PNG, WEBP})

#: Enough bytes for every header this module parses.
SNIFF_BYTES = 64

#: Refuse an image whose header claims more pixels than this. A decoded RGB
#: image at this size is roughly 1.5 GB, so the ceiling is about protecting
#: memory, not about photographic ambition.
MAX_IMAGE_PIXELS = 80_000_000

#: An ISO base-media brand that means audio even though the container is the
#: same one MP4 video uses.
_AUDIO_BRANDS = frozenset({b"M4A ", b"M4B ", b"M4P "})


def sniff(data: bytes, *, allowed: frozenset[MediaFormat] | None = None) -> SniffResult:
    """Identify ``data`` from its contents.

    ``allowed`` narrows the acceptable formats; omitting it accepts every
    format this module recognises. Raises ``UnsupportedMediaError`` when the
    bytes are unrecognised, when the format is outside ``allowed``, or when an
    image header describes more pixels than may safely be decoded.
    """
    if not data:
        raise UnsupportedMediaError("the media is empty")

    detected = _detect(data)
    if detected is None:
        raise UnsupportedMediaError(
            "the uploaded bytes are not a recognised image, video, audio or document format"
        )

    if allowed is not None and detected not in allowed:
        permitted = ", ".join(sorted(fmt.mime_type for fmt in allowed))
        raise UnsupportedMediaError(
            f"media of type {detected.mime_type} is not accepted here; expected one of {permitted}"
        )

    dimensions = _dimensions(detected, data) if detected.media_type is MediaType.IMAGE else None
    if dimensions is not None and dimensions.pixels > MAX_IMAGE_PIXELS:
        raise MediaTooLargeError(
            f"the image header describes {dimensions.width}x{dimensions.height} "
            f"({dimensions.pixels} pixels), above the {MAX_IMAGE_PIXELS} pixel ceiling"
        )
    return SniffResult(format=detected, dimensions=dimensions)


def verify_declared(declared: str | None, detected: MediaFormat) -> None:
    """Raise when a caller's declared type contradicts the detected one.

    A caller that declares nothing is fine — the bytes are authoritative
    either way. A caller that declares something *wrong* is telling us
    something, and the safe reading of it is refusal.
    """
    if declared is None:
        return
    claimed = declared.split(";", 1)[0].strip().lower()
    if not claimed or claimed == "application/octet-stream":
        return
    if claimed != detected.mime_type:
        raise UnsupportedMediaError(
            f"the upload declares {claimed} but its contents are {detected.mime_type}"
        )


def _detect(data: bytes) -> MediaFormat | None:
    """Return the format the leading bytes identify, or None."""
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return GIF
    if data.startswith(b"BM"):
        return BMP
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return TIFF
    if data.startswith(b"%PDF-"):
        return PDF
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        # EBML. WebM and Matroska share it; the doctype string decides.
        return WEBM if b"webm" in data[:SNIFF_BYTES] else MATROSKA
    if data.startswith(b"fLaC"):
        return FLAC
    if data.startswith(b"OggS"):
        return OGG
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE6) == 0xE2):
        return MP3

    if len(data) >= 12:
        if data[:4] == b"RIFF":
            container = data[8:12]
            if container == b"WEBP":
                return WEBP
            if container == b"WAVE":
                return WAV
            if container == b"AVI ":
                return AVI
        if data[4:8] == b"ftyp":
            brand = data[8:12]
            if brand in _AUDIO_BRANDS:
                return M4A
            if brand.startswith(b"qt"):
                return QUICKTIME
            return MP4
    return None


def _dimensions(fmt: MediaFormat, data: bytes) -> Dimensions | None:
    """Read pixel dimensions from an image header without decoding it."""
    try:
        if fmt is PNG:
            return _png_dimensions(data)
        if fmt is JPEG:
            return _jpeg_dimensions(data)
        if fmt is GIF:
            return _gif_dimensions(data)
        if fmt is WEBP:
            return _webp_dimensions(data)
    except (struct.error, IndexError, ValueError):
        # A header we cannot parse is not automatically hostile; the format is
        # already confirmed, and the pixel ceiling simply goes unchecked.
        return None
    return None


def _png_dimensions(data: bytes) -> Dimensions | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return Dimensions(width, height)


def _gif_dimensions(data: bytes) -> Dimensions | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return Dimensions(width, height)


def _webp_dimensions(data: bytes) -> Dimensions | None:
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return Dimensions(width, height)
    if chunk == b"VP8 ":
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return Dimensions(width, height)
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return Dimensions((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


#: JPEG start-of-frame markers carry the dimensions. The arithmetic and
#: progressive variants are separate markers for the same information.
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _jpeg_dimensions(data: bytes) -> Dimensions | None:
    """Walk JPEG segment headers to the frame that states the size."""
    offset = 2
    limit = len(data)
    while offset + 4 <= limit:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in {0xD8, 0x01} or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if marker == 0xFF:
            offset += 1
            continue
        segment_length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        if segment_length < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if offset + 9 > limit:
                return None
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return Dimensions(width, height)
        offset += 2 + segment_length
    return None
