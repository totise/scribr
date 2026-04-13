#!/usr/bin/env bash
# install.sh — Scribr installer for macOS
#
# Usage:
#   ./install.sh          # install into a new virtualenv at ~/.scribr/venv
#   ./install.sh --dev    # editable install from the current repo directory

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}${BOLD}==>${RESET} $*"; }
success() { echo -e "${GREEN}${BOLD}  ✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}${BOLD}  !${RESET} $*"; }
error()   { echo -e "${RED}${BOLD}  ✗${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── Config ─────────────────────────────────────────────────────────────────
INSTALL_DIR="$HOME/.scribr"
VENV_DIR="$INSTALL_DIR/venv"
LAUNCHER="$INSTALL_DIR/scribr"
REPO_URL="https://github.com/totise/scribr.git"
DEV_MODE=false

for arg in "$@"; do
  case "$arg" in
    --dev) DEV_MODE=true ;;
    --help|-h)
      echo "Usage: ./install.sh [--dev]"
      echo ""
      echo "  --dev   Install in editable mode from the current directory"
      echo "          instead of cloning from GitHub."
      exit 0
      ;;
    *) die "Unknown argument: $arg" ;;
  esac
done

# ── Banner ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}  Scribr Installer${RESET}"
echo -e "  macOS dictation app powered by NVIDIA Parakeet ASR"
echo ""

# ── Platform check ──────────────────────────────────────────────────────────
if [[ "$(uname)" != "Darwin" ]]; then
  die "Scribr only supports macOS. Detected: $(uname)"
fi
success "Platform: macOS $(sw_vers -productVersion)"

# ── Python check ────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.11; do
  if command -v "$candidate" &>/dev/null; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  fi
done

if [[ -z "$PYTHON" ]]; then
  error "Python 3.11 is required but was not found."
  echo ""
  echo "  Install it via Homebrew:"
  echo "    brew install python@3.11"
  echo ""
  echo "  Or download from: https://www.python.org/downloads/releases/"
  exit 1
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
success "Python: $PY_VERSION ($PYTHON)"

# ── RAM check (informational) ───────────────────────────────────────────────
TOTAL_RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if (( TOTAL_RAM_GB < 8 )); then
  warn "Only ${TOTAL_RAM_GB} GB RAM detected. The English model requires ~6 GB free."
  warn "You may run into out-of-memory issues. Consider using a lighter model."
else
  success "RAM: ${TOTAL_RAM_GB} GB"
fi

# ── Git check ───────────────────────────────────────────────────────────────
if ! command -v git &>/dev/null; then
  die "git is required but not installed. Install Xcode Command Line Tools: xcode-select --install"
fi

# ── Create install directory ────────────────────────────────────────────────
info "Installing into $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"

# ── Clone or use local source ────────────────────────────────────────────────
if [[ "$DEV_MODE" == true ]]; then
  # Verify we're in the repo root
  if [[ ! -f "$(pwd)/pyproject.toml" ]] || ! grep -q 'name = "scribr"' "$(pwd)/pyproject.toml" 2>/dev/null; then
    die "--dev must be run from the scribr repo root directory."
  fi
  SOURCE_DIR="$(pwd)"
  info "Dev mode: using source from $SOURCE_DIR"
else
  SOURCE_DIR="$INSTALL_DIR/src"
  if [[ -d "$SOURCE_DIR/.git" ]]; then
    info "Updating existing source clone"
    git -C "$SOURCE_DIR" pull --ff-only
  else
    info "Cloning Scribr from GitHub"
    git clone "$REPO_URL" "$SOURCE_DIR"
  fi
  success "Source ready"
fi

# ── Create virtualenv ────────────────────────────────────────────────────────
info "Creating virtual environment"
"$PYTHON" -m venv "$VENV_DIR"
PIP="$VENV_DIR/bin/pip"
PYTHON_VENV="$VENV_DIR/bin/python"
"$PIP" install --quiet --upgrade pip
success "Virtualenv created"

# ── Install PyTorch ──────────────────────────────────────────────────────────
info "Installing PyTorch (this may take a few minutes)"
# Use the CPU/MPS wheel; works on both Intel and Apple Silicon macOS
"$PIP" install --quiet torch torchaudio
success "PyTorch installed"

# ── Install NeMo ASR ─────────────────────────────────────────────────────────
info "Installing NeMo ASR toolkit (this may take several minutes)"
"$PIP" install --quiet "nemo_toolkit[asr]>=2.5.0"
success "NeMo installed"

# ── Install Scribr ───────────────────────────────────────────────────────────
info "Installing Scribr"
if [[ "$DEV_MODE" == true ]]; then
  "$PIP" install --quiet -e "$SOURCE_DIR"
else
  "$PIP" install --quiet "$SOURCE_DIR"
fi
success "Scribr installed"

# ── Write launcher script ────────────────────────────────────────────────────
info "Writing launcher to $LAUNCHER"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Scribr launcher — auto-generated by install.sh
exec "$VENV_DIR/bin/scribr" "\$@"
EOF
chmod +x "$LAUNCHER"
success "Launcher written"

# ── Symlink to /usr/local/bin if writable ────────────────────────────────────
SYMLINK_TARGET="/usr/local/bin/scribr"
if [[ -w "/usr/local/bin" ]]; then
  ln -sf "$LAUNCHER" "$SYMLINK_TARGET"
  success "Symlinked to $SYMLINK_TARGET"
else
  warn "Could not write to /usr/local/bin — adding $INSTALL_DIR to PATH instead."
  SHELL_RC=""
  if [[ "$SHELL" == */zsh ]]; then
    SHELL_RC="$HOME/.zshrc"
  elif [[ "$SHELL" == */bash ]]; then
    SHELL_RC="$HOME/.bash_profile"
  fi
  if [[ -n "$SHELL_RC" ]]; then
    if ! grep -q 'PATH.*\.scribr' "$SHELL_RC" 2>/dev/null; then
      echo "" >> "$SHELL_RC"
      echo "# Scribr" >> "$SHELL_RC"
      echo 'export PATH="$HOME/.scribr:$PATH"' >> "$SHELL_RC"
      success "Added $INSTALL_DIR to PATH in $SHELL_RC"
      warn "Run: source $SHELL_RC  (or open a new terminal)"
    fi
  fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  Scribr is installed!${RESET}"
echo ""
echo -e "  Run it with:  ${BOLD}scribr${RESET}"
echo ""
echo -e "  ${BOLD}First-run notes:${RESET}"
echo "  • macOS will prompt for Microphone access — allow it."
echo "  • If global hotkeys don't work, grant Accessibility access:"
echo "    System Settings → Privacy & Security → Accessibility"
echo "    Add your terminal app (or scribr itself) and enable it."
echo ""
echo -e "  ${BOLD}Hotkeys:${RESET}"
echo "  • Hold right Option    — record audio"
echo "  • Release right Option — transcribe + type result"
echo "  • Ctrl+Shift+Space     — switch language model"
echo ""
echo -e "  Config file: ${CYAN}~/.config/scribr/config.toml${RESET}"
echo ""
