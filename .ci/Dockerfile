FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y \
    software-properties-common gnupg2 curl && \
    add-apt-repository ppa:deadsnakes/ppa -y && \
    apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
    locales python3.13 python3.13-venv python3-pip git git-lfs gawk wget \
    diffstat unzip texinfo gcc build-essential chrpath socat cpio \
    python3-pexpect xz-utils debianutils iputils-ping python3-git \
    python3-jinja2 libegl1-mesa-dev libsdl2-dev pylint xterm file lz4 \
    zstd tmux rclone && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8 && update-locale LANG=en_US.UTF-8 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.13 2 && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 2

ENV LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8

# Install for the exact Python used by /usr/bin/env python3 in BitBake, without
# relying on Ubuntu's python3-boto3 targeting a different Python minor version.
RUN python3.13 -m venv --system-site-packages /opt/bitbake-python && \
    /opt/bitbake-python/bin/python -m pip install --no-cache-dir 'boto3>=1.35,<2' && \
    /opt/bitbake-python/bin/python -c 'import boto3, botocore'
ENV PATH="/opt/bitbake-python/bin:${PATH}"

RUN wget -q https://github.com/upx/upx/releases/download/v5.0.1/upx-5.0.1-amd64_linux.tar.xz && \
    tar xJf upx-5.0.1-amd64_linux.tar.xz && \
    install -m 0755 upx-5.0.1-amd64_linux/upx /usr/bin/upx && \
    rm -rf upx-5.0.1-amd64_linux.tar.xz upx-5.0.1-amd64_linux && \
    apt-get update && apt-get install -y --allow-downgrades tar=1.35+dfsg-3build1 && \
    rm -rf /var/lib/apt/lists/*

COPY bitbake /usr/lib/bitbake
RUN python3 -m compileall -q -j 0 /usr/lib/bitbake && \
    /usr/lib/bitbake/bin/bitbake-hashserv --help >/dev/null

# An empty named volume inherits this mode on its first mount.  The actual
# build and home directories are created at container runtime by the mapped
# host user, so the image is independent from a particular UID/GID.
RUN install -d -m 1777 /cache

VOLUME ["/cache"]
WORKDIR /workdir
CMD ["/bin/bash"]
