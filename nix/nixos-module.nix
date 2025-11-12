{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.services.vhotplug;
  inherit (lib)
    mkEnableOption
    mkOption
    mkIf
    types
    ;

  configFile = pkgs.writeText "vhotplug.json" (builtins.toJSON cfg.config);
in
{
  options.services.vhotplug = {
    enable = mkEnableOption "vhotplug device hot-plugging service";

    package = mkOption {
      type = types.package;
      default = pkgs.callPackage ./package.nix { };
      description = "The vhotplug package to use";
    };

    attachConnected = mkOption {
      type = types.bool;
      default = false;
      description = "Attach already connected devices on startup";
    };

    debug = mkOption {
      type = types.bool;
      default = false;
      description = "Enable debug logging";
    };

    config = mkOption {
      type = types.attrs;
      default = {
        usbPassthrough = [ ];
        pciPassthrough = [ ];
        vms = [ ];
        general = { };
      };
      description = ''
        vhotplug configuration as a Nix attribute set.
        This will be converted to JSON and used as the config file.
      '';
      example = lib.literalExpression ''
        {
          usbPassthrough = [
            {
              description = "Devices for VM1";
              targetVm = "vm1";
              allow = [
                {
                  interfaceClass = 3;
                  interfaceProtocol = 2;
                  description = "HID Mouse";
                }
              ];
            }
          ];
          vms = [
            {
              name = "vm1";
              type = "qemu";
              socket = "/tmp/qmp-socket1";
            }
          ];
        }
      '';
    };
  };

  config = mkIf cfg.enable {
    systemd.services.vhotplug = {
      description = "vhotplug - Device hot-plugging service for virtual machines";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      serviceConfig = {
        Type = "simple";
        ExecStart = lib.concatStringsSep " " (
          [
            "${cfg.package}/bin/vhotplug"
            "--config ${configFile}"
          ]
          ++ lib.optional cfg.attachConnected "--attach-connected"
          ++ lib.optional cfg.debug "--debug"
        );
        Restart = "on-failure";
        RestartSec = "5s";
      };
    };
  };
}
