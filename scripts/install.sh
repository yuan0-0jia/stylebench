#!/bin/sh
# StyleBench installer
# Usage:
#   curl -LsSf https://raw.githubusercontent.com/yuan0-0jia/stylebench/main/scripts/install.sh | sh
#   curl -LsSf ... | sh -s -- ~/work/stylebench
set -e

INSTALL_DIR="${1:-$HOME/stylebench}"
REPO_URL="https://github.com/yuan0-0jia/stylebench.git"

echo "StyleBench installer"
echo "===================="
echo "Install directory: $INSTALL_DIR"
echo ""

# Install uv if not found
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    if [ -f "$HOME/.local/bin/env" ]; then
        . "$HOME/.local/bin/env"
    fi
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "Using uv: $(command -v uv)"

# Clone the repo
if [ -d "$INSTALL_DIR" ]; then
    echo "Directory $INSTALL_DIR already exists, pulling latest..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "Cloning stylebench..."
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

# Install dependencies and create venv
echo "Installing dependencies..."
cd "$INSTALL_DIR"
uv sync

echo ""
echo "Installation complete!"
echo ""
echo "Add to your shell profile:"
echo "  export PATH=\"$INSTALL_DIR/.venv/bin:\$PATH\""
echo ""
echo "Then run:"
echo "  stylebench --help"
echo "  stylebench setup-data    # download benchmark data"
echo "  stylebench run            # run the benchmark"
