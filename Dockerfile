FROM debian:bookworm-slim

ARG PIXI_VERSION=0.40.0
ARG OPENVSCODE_VERSION=1.96.2
ARG TTYD_VERSION=1.7.7
ARG SUPERVISORD_VERSION=0.7.3

ENV HOME=/home/pixi
ENV USER=pixi
ENV PATH="/home/pixi/.pixi/bin:/home/pixi/.local/bin:${PATH}"
ENV WORKSPACE_DIR=/home/pixi/workspace

# System packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git vim jq htop openssh-client ca-certificates \
    build-essential procps lsof net-tools unzip xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Create user
RUN groupadd -g 1000 pixi && \
    useradd -m -u 1000 -g pixi -s /bin/bash pixi

# Install pixi
RUN curl -fsSL https://pixi.sh/install.sh | PIXI_HOME=/home/pixi/.pixi bash && \
    chmod +x /home/pixi/.pixi/bin/pixi

# Install supervisord (Go version - lightweight)
RUN wget -qO /usr/local/bin/supervisord \
    "https://github.com/ochinchina/supervisord/releases/download/v${SUPERVISORD_VERSION}/supervisord_${SUPERVISORD_VERSION}_linux_amd64" && \
    chmod +x /usr/local/bin/supervisord

# Install OpenVSCode Server
RUN wget -qO /tmp/openvscode.tar.gz \
    "https://github.com/nicknisi/openvscode-releases/releases/download/${OPENVSCODE_VERSION}/openvscode-server-v${OPENVSCODE_VERSION}-linux-x64.tar.gz" && \
    mkdir -p /opt/openvscode && \
    tar -xzf /tmp/openvscode.tar.gz -C /opt/openvscode --strip-components=1 && \
    rm /tmp/openvscode.tar.gz

# Install ttyd
RUN wget -qO /usr/local/bin/ttyd \
    "https://github.com/nicknisi/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" && \
    chmod +x /usr/local/bin/ttyd

# Install Python packages for API server
RUN /home/pixi/.pixi/bin/pixi global install python && \
    /home/pixi/.pixi/bin/pip install fastapi uvicorn

# Copy API server
COPY api_server/ /opt/devcontainer-api/api_server/

# Copy supervisord configs
COPY supervisord.d/ /etc/supervisor/conf.d/
COPY supervisord.conf /etc/supervisord.conf

# Create workspace and log directories
RUN mkdir -p /home/pixi/workspace /var/log && \
    chown -R pixi:pixi /home/pixi /var/log /opt/devcontainer-api

USER pixi
WORKDIR /home/pixi

EXPOSE 3000 7681 8080

CMD ["/usr/local/bin/supervisord", "-c", "/etc/supervisord.conf"]
