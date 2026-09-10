# Small-dataset demonstration

The Vision page contains the media demonstration console. It uses the signed-in
user's existing credential; every job is scoped to that subject. Backend processing is
serialized, survives page refreshes, and preserves results in the dedicated demo SQLite
database. Interrupted jobs are marked failed at service restart.

Start with the clearly labeled synthetic image/audio/video buttons. For a permitted
Maldives clip, upload an MP4 below 20 MB and preferably 15–30 seconds. Video analyses
three timestamps from its first minute. Select the actual speech language (English or
Dhivehi); Latin Dhivehi is an output script, not a spoken-language selection.

Review visible objects, readable text, sampled timestamps and transcript against the
source. Record misses and incorrect statements. Descriptions do not establish a person's
identity or intent. Landmark names are model suggestions and must be checked.

## Acceptance status

- Labeled synthetic image/audio/video: previously executed on cyber-ai; re-run through
  the web queue to validate the deployed workflow.
- Dhivehi speech and OCR: cached artifacts load; no reviewed real input supplied yet.
- Latin transliteration: known expansion failure; demo runner rejects extreme word
  expansion instead of presenting that output as a successful result.
- Mixed-language audio, exact speaker separation, full-page OCR and continuous video
  tracking are not validated by this small demonstration.

The user-selected YouTube URL/local clip is still required. No remote URL fetcher is
exposed by the demo service. Use an authorized local excerpt. Synthetic fixtures verify
runtime behavior; they are not a Dhivehi accuracy benchmark.

Deployment overlay: `docker-compose.media-demo.yml`, used alongside existing base,
deploy and GPU overlays. The media service reuses installed model caches, requires
language authorization, and publishes only on the cyber-ai Tailscale address at 8020.
Local frontend: set `FACEID_API_URL=http://100.74.113.94:8000` and
`FACEID_MEDIA_DEMO_URL=http://100.74.113.94:8020`.
# Additional vision models

Run `python /demo/install_vision.py` once against the persistent `/cache`
volume. It installs pinned RT-DETR object detection, SigLIP2 image embeddings,
and SAM2-small segmentation artifacts and writes `vision-models.json` as the
deployment receipt. The image includes Supervision's ByteTrack implementation;
tracking integration must preserve evidence timestamps and stable per-session
IDs rather than treating a track as a confirmed identity.
