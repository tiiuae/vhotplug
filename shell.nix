{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    pkgs.gcc
    pkgs.python311Full
    pkgs.python311Packages.virtualenv
    pkgs.python311Packages.pyudev
    pkgs.python311Packages.inotify-simple
    pkgs.python311Packages.psutil
    pkgs.python311Packages.pyudev
  ];

  shellHook = ''
    if [ ! -d .venv ]; then
      virtualenv .venv
      source .venv/bin/activate
    else
      source .venv/bin/activate
    fi
    echo "Welcome to your Python development environment."
  '';
}
