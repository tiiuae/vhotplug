import json
import logging
import os
import threading
import pprint
from vhotplug.device import *

logger = logging.getLogger("vhotplug")

class GhafPolicy:
    def __init__(self, policy_path):
        self.lock = threading.Lock()
        self.extra_allowed = None;
        with open(policy_path, 'r') as file:
           json_data  = json.load(file)
           usb_rules = json_data["usb"]
           self.evdev_hotplug_rules = json_data["eventDevices"]

        self.usb_hotplug_rules = usb_rules.get("hotplug_rules", {})
        self.usb_extra_devices = usb_rules.get("static_devices", []);
        logger.debug(f"{self.evdev_hotplug_rules} \n{self.usb_hotplug_rules} \n{self.usb_extra_devices}")
        self.denylist = self.usb_hotplug_rules.get("denylist", {})
        self.allowlist = self.usb_hotplug_rules.get("allowlist", {})
        self.class_rules = self.usb_hotplug_rules.get("classlist", {})
        self.allow_static_devices()
        self.vm_list = None

    def allow_static_devices(self, force = False):
        for device in self.usb_extra_devices:
            vendor = device.get("vendorId", None)
            product = device.get("productId", None)
            vms = device.get("vms", None)
            if vendor is not None and product is not None and vms is not None:
                vendor_product = f"0x{vendor}:0x{product}"

                if force == True:
                    self.allowlist[vendor_product] = vms
                elif vendor_product not in self.allowlist:
                    self.allowlist[vendor_product] = vms
                else:
                    logger.info(f"Product is already in allowlist: {vendor_product} allowed VMs are {self.allowlist[vendor_product]}")

    def update_policy(self, policy):
        force_static_devices = False
        with self.lock:
            self.usb_hotplug_rules = policy
            if "denylist" in self.usb_hotplug_rules:
                self.denylist = self.usb_hotplug_rules.get("denylist", self.denylist)

            if "allowlist" in self.usb_hotplug_rules:
                self.allowlist = self.usb_hotplug_rules.get("allowlist", self.allowlist)

            if "classlist" in self.usb_hotplug_rules:
                self.class_rules = self.usb_hotplug_rules.get("classlist", self.class_rules)

            if "static_devices" in self.usb_hotplug_rules:
                self.usb_extra_devices = self.usb_hotplug_rules.get("static_devices")
                force_static_devices = True

            self.allow_static_devices(force_static_devices)
            self.vm_list = None

    def vm_for_evdev_devices(self):
        vm = {}
        busPrefix = None
        if "pcieBusPrefix" in self.evdev_hotplug_rules and "targetVM" in self.evdev_hotplug_rules:
            vm_name = self.evdev_hotplug_rules["targetVM"]
            busPrefix = self.evdev_hotplug_rules["pcieBusPrefix"]
            vm["name"] = vm_name
            vm["qmpSocket"] = f"/var/lib/microvms/{vm_name}/{vm_name}.sock"

        return vm, busPrefix

    def vm_for_usb_device(self, vid, pid, vendor_name, product_name, interfaces):
        with self.lock:
            try:
                logger.info(f"Searching for a VM for {vid}:{pid}, {vendor_name}:{product_name}")
                usb_interfaces = parse_usb_interfaces(interfaces)
                for interface in usb_interfaces:
                    device_class = interface["class"]
                    subclass = interface["subclass"]
                    protocol = interface["protocol"]
                    vendor = f"0x{vid}".lower()
                    product = f"0x{pid}".lower()
                    dclass = f"{device_class:#04x}".lower()
                    sclass = f"{subclass:#04x}".lower()
                    protoc = f"{protocol:#04x}".lower()
                    vms = self.get_allowed_vms(dclass, sclass, protoc, vendor, product)
                    vm = {}
                    if len(vms):
                        if len(vms) > 1:
                            logger.warning(f"More than one VM can access this device. Passing through to vm: {vms[0]}.")
                        vm["name"] = vms[0]
                        vm["qmpSocket"] = f"/var/lib/microvms/{vms[0]}/{vms[0]}.sock"
                        return vm
                    else:
                        return None
            except Exception as e:
                    logger.error(f"Failed to find VM for USB device in the configuration file: {e}")
            return None

    def get_all_vms(self):
        if self.vm_list is not None:
            return self.vm_list
        self.vm_list = []
        vms_by_name = []
        if "targetVM" in self.evdev_hotplug_rules:
            vmname = self.evdev_hotplug_rules["targetVM"]
            vms_by_name.append(vmname)
        for _, vms in self.allowlist.items():
            for vm in vms:
                if vm not in vms_by_name:
                    vms_by_name.append(vm)

        for _, vms in self.class_rules.items():
            for vm in vms:
                if vm not in vms_by_name:
                    vms_by_name.append(vm)

        for vmname in vms_by_name:
            self.vm_list.append( {"name": vmname,
                                  "qmpSocket":f"/var/lib/microvms/{vmname}/{vmname}.sock"})
        return self.vm_list

    def lookup(self, allowlist: dict, key: any) -> list:
        return allowlist.get(key, [])

    def not_allowed(self, vendor_id: any, product_id: any) -> bool:
        disallowed_products = self.denylist.get(vendor_id)
        if disallowed_products is not None:
            return product_id in disallowed_products
        else:
            neg_vendor = f"~{vendor_id}"
            allowed_products = self.denylist.get(neg_vendor)
            if allowed_products is not None:
                return product_id not in allowed_products
            else:
                return False

    def get_allowed_vms(self, device_class: int, subclass: int, protocol: int, vendor_id: int, product_id: int):
        # Check if the device is not allowed
        if self.not_allowed(vendor_id, product_id):
            return []

        # Check if the device is mapped to a specific VM
        device_key_0 = f"{vendor_id}:{product_id}"
        device_key_1 = f"{vendor_id}:*"
        vms_by_device = self.lookup(self.allowlist, device_key_0) + self.lookup(self.allowlist, device_key_1)
        if len(vms_by_device) > 0:
            unique_vms_by_device = list(dict.fromkeys(vms_by_device))
            return unique_vms_by_device

        # Based on class, subclass, and protocol find list VMs which can access it
        class_key_0 = f"{device_class}:{subclass}:{protocol}"
        class_key_1 = f"{device_class}:{subclass}:*"
        class_key_2 = f"{device_class}:*:{protocol}"
        class_key_3 = f"{device_class}:*:*"
        cl_01_vms = self.lookup(self.class_rules, class_key_0) + self.lookup(self.class_rules, class_key_1)
        cl_23_vms = self.lookup(self.class_rules, class_key_2) + self.lookup(self.class_rules, class_key_3)
        vms_by_class = cl_01_vms + cl_23_vms

        # Merge VMs from all above rules
        if len(vms_by_class) > 0:
            unique_vms_by_class = list(dict.fromkeys(vms_by_class))
        else:
            unique_vms_by_class = []

        return unique_vms_by_class


############TESTS###############
class UnitTest:
    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        static_policy = os.path.join(current_dir, '../testdata/', 'config.json')
        self.policy = GhafPolicy(static_policy)

    def print_vmlist(self):
        pprint.pprint("VM LIST:")
        pprint.pprint(self.policy.get_all_vms())
        res = self.policy.vm_for_evdev_devices()
        vm = res[0]
        prefix = res[1]
        print("\nEVDEV Passthrough:")
        pprint.pprint("VM:")
        pprint.pprint(vm)
        pprint.pprint("PCI Bus Prefix:")
        pprint.pprint(prefix)
        print("\n")


    def compare_results(self, list1, list2):
        if len(list1) == len(list2):
            for elm in list1:
                if elm not in list2:
                    return "❌ FAIL"
            return "✅ PASS"
        return "❌ FAIL"

    def remove_comments(self, json_as_string):
        result = ""
        for line in json_as_string.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            # Remove inline comment
            code_part = line.split('#', 1)[0].rstrip()
            if code_part:
                result += code_part + "\n"
        return result

    def run_test(self, test_id, device_class, subclass, vendor_id, product_id, protocol, expected_vms):
        vms = self.policy.get_allowed_vms(
            device_class=device_class,
            subclass=subclass,
            vendor_id=vendor_id,
            product_id=product_id,
            protocol=protocol
        )
        result = self.compare_results(expected_vms, vms)
        print(f"{test_id}: expected: {str(expected_vms):<30} received: {str(vms):<30} Result: {result}")


if __name__ == "__main__":
    # To run this unittest comment this line 'from vhotplug.device import *'
    unittest = UnitTest()
    unittest.print_vmlist()

    unittest.run_test(
        test_id="TEST1",
        device_class="0xff",
        subclass="0x01",
        vendor_id="0x0b95",
        product_id="0x1790",
        protocol=0,
        expected_vms=['net-vm']
    )

    unittest.run_test(
        test_id="TEST2",
        device_class="0x01",
        subclass="0x02",
        vendor_id="0xdead",
        product_id="0xbeef",
        protocol="0x01",
        expected_vms=['audio-vm']
    )

    unittest.run_test(
        test_id="TEST3",
        device_class="0x0e",
        subclass="0x02",
        vendor_id="0x04f2",
        product_id="0xb751",
        protocol="0x01",
        expected_vms=["chrome-vm"]
    )

    unittest.run_test(
        test_id="TEST4",
        device_class="0x0e",
        subclass="0x02",
        vendor_id="0x04f2",
        product_id="0xb755",
        protocol="0x01",
        expected_vms=["chrome-vm"]
    )

    unittest.run_test(
        test_id="TEST5",
        device_class="0xe0",
        subclass="0x01",
        vendor_id="0x04f2",
        product_id="0xb755",
        protocol="0x01",
        expected_vms=["gui-vm"]
    )

    unittest.run_test(
        test_id="TEST6",
        device_class="0xe0",
        subclass="0x01",
        vendor_id="0xbadb",
        product_id="0xdada",
        protocol="0x01",
        expected_vms=[]
    )

    unittest.run_test(
        test_id="TEST7",
        device_class="0xe0",
        subclass="0x01",
        vendor_id="0xbabb",
        product_id="0xcaca",
        protocol="0x01",
        expected_vms=["gui-vm"]
    )

    unittest.run_test(
        test_id="TEST8",
        device_class="0xe0",
        subclass="0x01",
        vendor_id="0xbabb",
        product_id="0xb755",
        protocol="0x01",
        expected_vms=[]
    )
