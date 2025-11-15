{
  python3Packages,
  lib,
}:

python3Packages.buildPythonApplication {
  pname = "vhotplug";
  version = "1.0";

  src = ./..;

  pyproject = true;

  build-system = with python3Packages; [
    setuptools
  ];

  dependencies = with python3Packages; [
    inotify-simple
    psutil
    pyudev
    qemu-qmp
  ];

  doCheck = false;

  meta = with lib; {
    description = "Hot-plugging USB and PCI devices to virtual machines";
    homepage = "https://github.com/tiiuae/vhotplug";
    license = licenses.asl20;
    platforms = platforms.linux;
    maintainers = [ ];
    mainProgram = "vhotplug";
  };
}
