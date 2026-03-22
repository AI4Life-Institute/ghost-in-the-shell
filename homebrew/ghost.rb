# Formula for: brew install ai4life/tap/ghost
#
# Deploy to: https://github.com/AI4Life-Institute/homebrew-tap
#   homebrew-tap/
#   └── Formula/
#       └── ghost.rb   ← this file
#
# Update sha256 on each release:
#   curl -sL https://github.com/AI4Life-Institute/ghost-in-the-shell/archive/refs/tags/vX.Y.Z.tar.gz | shasum -a 256

class Ghost < Formula
  desc "Control AI coding agents on your machine via Discord"
  homepage "https://github.com/AI4Life-Institute/ghost-in-the-shell"
  url "https://github.com/AI4Life-Institute/ghost-in-the-shell/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "f0a4072b610401fe7298436899a6a75b147c80daa943bcc5fedb637f4858d6e6"
  license "Nonstandard"

  head "https://github.com/AI4Life-Institute/ghost-in-the-shell.git", branch: "master"

  depends_on "python@3.12"
  depends_on "tmux"
  depends_on "uv" => :build

  def install
    # Create isolated virtualenv and install ghost + all pip dependencies via uv
    system Formula["uv"].opt_bin/"uv", "venv", libexec.to_s,
           "--python", Formula["python@3.12"].opt_bin/"python3.12"

    system Formula["uv"].opt_bin/"uv", "pip", "install", buildpath.to_s,
           "--python", (libexec/"bin/python3").to_s

    bin.install_symlink libexec/"bin/ghost"
    bin.install_symlink libexec/"bin/gits"
  end

  def post_install
    # Launch setup wizard immediately after install
    system bin/"ghost"
  end

  def caveats
    <<~EOS
      Run the setup wizard anytime to reconfigure:

        ghost

      To restart the background service:

        ghost restart
    EOS
  end

  test do
    assert_match "ghost", shell_output("#{bin}/ghost --help 2>&1")
  end
end
