{
  description = "vhotplug - Device hot-plugging service for virtual machines";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    flake-parts = {
      url = "github:hercules-ci/flake-parts";
      inputs.nixpkgs-lib.follows = "nixpkgs";
    };

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      imports = [
        inputs.treefmt-nix.flakeModule
      ];

      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      perSystem =
        {
          config,
          self',
          pkgs,
          ...
        }:
        let
          pythonDeps = with pkgs.python3.pkgs; [
            inotify-simple
            psutil
            pyudev
            qemu-qmp
            pytest
          ];
          pythonTypeDeps = pythonDeps ++ [
            pkgs.python3.pkgs.types-psutil
          ];
        in
        {
          # Package definition
          packages = {
            default = pkgs.python3Packages.buildPythonApplication {
              pname = "vhotplug";
              version = "1.0";

              src = ./.;

              format = "setuptools";

              propagatedBuildInputs = pythonDeps;

              # Skip tests if they exist
              doCheck = false;

              meta = with pkgs.lib; {
                description = "Hot-plugging USB and PCI devices to virtual machines";
                homepage = "https://github.com/tiiuae/vhotplug";
                license = licenses.asl20;
                platforms = platforms.linux;
                maintainers = [ ];
              };
            };
          };

          # Development shell
          devShells.default = pkgs.mkShell {
            inputsFrom = [ self'.packages.default ];

            packages = with pkgs; [
              # Python development tools
              python3Packages.pytest
              python3Packages.mypy
              python3Packages.pylint
              # python3Packages.ruff

              # Formatting
              config.treefmt.build.wrapper
            ];

            shellHook = ''
              echo "vhotplug development environment"
              echo "Run 'treefmt' to format code"
              echo "Run 'python -m pytest' to run tests"
            '';
          };

          # treefmt configuration
          treefmt = {
            projectRootFile = "flake.nix";

            programs = {
              # Python formatting
              # ruff-format.enable = true;
              # TODO: maybe instead of pylint?
              # ruff-check.enable = true;

              # Nix formatting
              nixfmt.enable = true;

              # JSON formatting
              prettier = {
                enable = true;
                includes = [
                  "*.json"
                  "*.md"
                ];
              };

              # Type checking
              mypy.enable = true;
              mypy.directories = {
                "vhotplug" = {
                  extraPythonPackages = pythonTypeDeps;
                };
                "vhotplugcli" = {
                  extraPythonPackages = pythonTypeDeps;
                };
                "tests" = {
                  extraPythonPackages = pythonTypeDeps;
                };
              };
            };
          };
        };
    };
}
