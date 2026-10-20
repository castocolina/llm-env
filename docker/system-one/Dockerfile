# Laya (Jev-compatible System One server) on AMD ROCm. Upstream's compose
# covers only NVIDIA/CPU, so llm-env builds this image locally:
# scripts/system-one-build.sh (make system-one-build / make setup).
FROM python:3.11-slim-trixie
ARG TORCH_VERSION=2.14.1
# shortcut: ROCR 1.21 AsyncEventsLoop busy-spins one core after the first GPU kernel (https://github.com/ROCm/rocm-systems/issues/12674, https://github.com/ROCm/TheRock/issues/7051); move back to the newest index once a release carries rocm-systems PR #7898; re-run the idle check.
ARG TORCH_INDEX=rocm7.2
ARG LAYA_VERSION=0.4.2
ARG GPU_ARCH
ENV PIP_NO_CACHE_DIR=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false \
    TORCH_DISABLE_NATIVE_JIT=1 LAYA_DEVICE=cuda LAYA_PRELOAD=1 \
    HF_HOME=/home/laya/.cache/huggingface
# Drop per-arch kernels for every arch except GPU_ARCH, its family dir (last char → x), and gfx<major>-generic.
# Same RUN as the install: a later RUN would leave the deleted files in the lower layer.
RUN test -n "${GPU_ARCH}" \
    && pip install "torch==${TORCH_VERSION}" --index-url https://download.pytorch.org/whl/${TORCH_INDEX} \
    && pip install "laya[serve]==${LAYA_VERSION}" "huggingface_hub==2.2.0" \
    && family="${GPU_ARCH%?}x" && major="${GPU_ARCH#gfx}" && major="${major%??}" && generic="gfx${major}-generic" \
    && site=/usr/local/lib/python3.11/site-packages \
    && find "$site/torch" "$site/triton" -type f -path '*gfx*' \
         ! -path "*${GPU_ARCH}*" ! -path "*${family}*" ! -path "*${generic}*" -delete \
    && find "$site/torch" "$site/triton" -type d -empty -delete
# libatomic1: torch ROCm ImportError without it; libnuma1/libdrm2: rocSHMEM/HIP warnings.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 libdrm2 libnuma1 \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 10001 laya && useradd -u 10001 -g laya -m laya \
    && mkdir -p "$HF_HOME" && chown -R laya:laya /home/laya
COPY --chown=10001:10001 phoenix_observer.py /opt/laya-phoenix-observer.py
USER laya
EXPOSE 8000
CMD ["python3", "/opt/laya-phoenix-observer.py"]
