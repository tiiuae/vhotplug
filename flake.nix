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
    inputs@{ self, flake-parts, ... }:
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

      flake.nixosModules.default = ./nix/nixos-module.nix;

      perSystem =
        {
          config,
          self',
          pkgs,
          system,
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
            default = pkgs.callPackage ./nix/package.nix { };
          };

          # NixOS tests (only x86_64-linux for now due to KVM requirement - and not having aarch64 CI runner with KVM)
          checks = pkgs.lib.optionalAttrs (system == "x86_64-linux") {
            vhotplug-service = pkgs.callPackage ./nix/nixos-test.nix { inherit self; };
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
