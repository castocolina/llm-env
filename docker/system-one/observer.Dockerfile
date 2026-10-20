# Fast, reproducible derivative for the already-installed local ROCm image.
# This avoids reinstalling Torch/ROCm when only observer code changes.
ARG BASE_IMAGE=localhost/llm-env-system-one:rocm
FROM ${BASE_IMAGE}
COPY phoenix_observer.py /opt/laya-phoenix-observer.py
CMD ["python3", "/opt/laya-phoenix-observer.py"]
