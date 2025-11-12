{
  pkgs,
  self,
  ...
}:

pkgs.testers.runNixOSTest {
  name = "vhotplug-service";

  nodes.machine = {
    imports = [ self.nixosModules.default ];

    services.vhotplug = {
      enable = true;
      debug = true;
      config = {
        usbPassthrough = [
          {
            description = "Test USB rule";
            targetVm = "testvm";
            allow = [
              {
                interfaceClass = 3;
                interfaceProtocol = 2;
                description = "HID Mouse";
              }
            ];
          }
        ];
        pciPassthrough = [ ];
        vms = [
          {
            name = "testvm";
            type = "qemu";
            socket = "/tmp/qmp-socket-test";
          }
        ];
        general = {
          api = {
            enable = false;
          };
        };
      };
    };
  };

  testScript = ''
    machine.start()
    machine.wait_for_unit("vhotplug.service")
    machine.succeed("systemctl status vhotplug.service")

    # Verify vhotplug is waiting for devices
    machine.wait_until_succeeds("journalctl -u vhotplug.service | grep -q 'Waiting for new devices'")
  '';
}
