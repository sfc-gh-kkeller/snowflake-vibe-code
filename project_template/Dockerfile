# Multi-tenant SPCS DevContainer with nginx front-router
# Based on https://github.com/sfc-gh-kkeller/spcs-devcontainer
# All traffic enters via nginx on port 8080

ARG DEBIAN_VERSION=bookworm
FROM debian:${DEBIAN_VERSION}-slim

ARG OPENVSCODE_VERSION=1.109.5
ARG TTYD_VERSION=1.7.7
ARG SUPERVISORD_VERSION=0.7.3

ENV DEBIAN_FRONTEND=noninteractive

# System packages + nginx
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    git \
    vim \
    sed \
    gawk \
    grep \
    procps \
    psmisc \
    openssh-client \
    sudo \
    bash-completion \
    less \
    htop \
    jq \
    unzip \
    tar \
    gzip \
    xz-utils \
    build-essential \
    python3 \
    python3-pip \
    nginx \
    && rm -rf /var/lib/apt/lists/*

# Create pixi user with sudo
RUN useradd -m -s /bin/bash -u 1000 pixi \
    && echo "pixi ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/pixi \
    && chmod 0440 /etc/sudoers.d/pixi

# Install OpenVSCode Server
RUN curl -fsSL \
    "https://github.com/gitpod-io/openvscode-server/releases/download/openvscode-server-v${OPENVSCODE_VERSION}/openvscode-server-v${OPENVSCODE_VERSION}-linux-x64.tar.gz" \
    | tar -xz -C /opt \
    && mv /opt/openvscode-server-v${OPENVSCODE_VERSION}-linux-x64 /opt/openvscode-server \
    && ln -s /opt/openvscode-server/bin/openvscode-server /usr/local/bin/openvscode-server

# Install ttyd
RUN curl -fsSL \
    "https://github.com/tsl0922/ttyd/releases/download/${TTYD_VERSION}/ttyd.x86_64" \
    -o /usr/local/bin/ttyd \
    && chmod +x /usr/local/bin/ttyd

# Install supervisord (Go version - lightweight)
RUN curl -fsSL \
    "https://github.com/ochinchina/supervisord/releases/download/v${SUPERVISORD_VERSION}/supervisord_${SUPERVISORD_VERSION}_Linux_64-bit.tar.gz" \
    | tar -xz -C /tmp \
    && mv /tmp/supervisord_${SUPERVISORD_VERSION}_Linux_64-bit/supervisord /usr/local/bin/ \
    && chmod +x /usr/local/bin/supervisord \
    && rm -rf /tmp/supervisord*


# Install GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Switch to pixi user for pixi + asdf install
USER pixi
WORKDIR /home/pixi

# Install pixi
RUN curl -fsSL https://pixi.sh/install.sh | bash

# Install snow CLI
RUN pip install --break-system-packages --user snowflake-cli || true

# Install CoCo CLI (cortex)
RUN curl -fsSL https://ai.snowflake.com/static/cc-scripts/install.sh | sh || true

# Install asdf (v0.20+ is a Go binary)
RUN mkdir -p ~/.local/bin && \
    curl -fsSL https://github.com/asdf-vm/asdf/releases/download/v0.20.0/asdf-v0.20.0-linux-amd64.tar.gz | \
    tar -xz -C ~/.local/bin


# Shell configuration
RUN echo 'eval "$(~/.pixi/bin/pixi completion --shell bash)"' >> ~/.bashrc \
    && echo 'export PATH="$HOME/.pixi/bin:$HOME/.local/bin:$PATH"' >> ~/.bashrc

# Pre-install VS Code extensions (Snowflake)
RUN /opt/openvscode-server/bin/openvscode-server --install-extension snowflake.snowflake-vsc || true

# Back to root for system config
USER root

# Skeleton for first-boot copy
RUN mkdir -p /etc/skel.pixi \
    && cp -r /home/pixi/. /etc/skel.pixi/ \
    && chown -R root:root /etc/skel.pixi

# nginx config
COPY nginx.conf /etc/nginx/nginx.conf
RUN mkdir -p /etc/nginx/conf.d \
    && chown -R pixi:pixi /var/log/nginx /var/lib/nginx /run

# supervisord config
COPY supervisord.conf /etc/supervisord.conf

# API server, backup daemon, and token refresh
COPY server.py /opt/api/server.py
COPY backup.py /opt/api/backup.py
COPY token_refresh.py /opt/api/token_refresh.py
COPY supervisord-devmode.conf /opt/api/supervisord-devmode.conf
COPY devserver-wrapper.sh /opt/api/devserver-wrapper.sh
RUN chmod +x /opt/api/devserver-wrapper.sh

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Directories and permissions
RUN mkdir -p /home/pixi/workspace /tmp/nginx \
    && chown -R pixi:pixi /home/pixi /tmp/nginx

# Default fallback page when no dev server is running
RUN mkdir -p /var/www/default && echo '<!DOCTYPE html><html><head><title>Dev Container</title><style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#1a1a2e;color:#e0e0e0}a{color:#4fc3f7;margin:0 1em}</style></head><body><div style="text-align:center"><h1>Dev Container Ready</h1><p>No app running on port 4000 yet.</p><p><a href="/vscode/">VS Code</a> <a href="/terminal/">Terminal</a></p></div></body></html>' > /var/www/default/502.html \
    && echo '<!DOCTYPE html><html><head><title>Dev Mode Disabled</title><style>body{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;background:#1a1a2e;color:#e0e0e0}code{background:#333;padding:2px 8px;border-radius:4px}</style></head><body><div style="text-align:center"><h1>Dev Mode Not Enabled</h1><p>VS Code and terminal are disabled by default.</p><p>Enable via MCP: <code>enable_dev_mode(project_id)</code></p></div></body></html>' > /var/www/default/devmode-disabled.html

ENV HOME=/home/pixi
ENV USER=pixi
ENV PATH="/home/pixi/.pixi/bin:/home/pixi/.local/bin:/usr/local/bin:/usr/bin:/bin"

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/local/bin/supervisord", "-c", "/etc/supervisord.conf"]
