# The clip renderer as a Cloudflare container. Built by GitHub Actions
# (.github/workflows/deploy-renderer.yml) with the repo root as build context.
# Debian 13's ffmpeg (7.1) has everything the renderer uses: libx264, aac,
# loudnorm, concat, and https input for reading masters from R2 by byte range.
# (Debian 12's ffmpeg 5.1 failed on the R2 input, on Shockwave.) OpenCV finds
# the faces for the 9:16 and 1:1 crops; its face models ship inside the wheel.
FROM python:3.12-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pillow==12.0.0 boto3==1.35.36 opencv-python-headless==4.10.0.84 "numpy<2.3"

WORKDIR /app
COPY pipeline/clip_render.py pipeline/clip_worker.py pipeline/
COPY site/index.json site/
COPY site/clipdata site/clipdata
COPY site/assets/compliance.json site/assets/
COPY brand/fonts brand/fonts
COPY brand/logo-white.png brand/
COPY cloud/server.py cloud/

ENV PYTHONUNBUFFERED=1 WORKER_ID=cloud
EXPOSE 8080
CMD ["python3", "cloud/server.py"]
