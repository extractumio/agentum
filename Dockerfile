FROM ubuntu:24.04

LABEL org.opencontainers.image.title="agentum"

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-venv \
        python3-pip \
        python3-dev \
        build-essential \
        ca-certificates \
        curl \
        git \
        nodejs \
        npm \
        bubblewrap \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv ${VIRTUAL_ENV}
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

WORKDIR /

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

COPY . /

# Copy and make entrypoint executable
COPY entrypoint-web.sh /entrypoint-web.sh
RUN chmod +x /entrypoint-web.sh

ENV AGENTUM_ROOT=/
ENV PYTHONPATH=/
ENV PYTHONUNBUFFERED=1
