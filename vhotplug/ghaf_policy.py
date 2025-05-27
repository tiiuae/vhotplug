import json
import logging
import os
import threading
from vhotplug.device import *

logger = logging.getLogger("vhotplug")

class GhafPolicy:
    def __init__(self, policy_path, evdev_config = None):
        self.lock = threading.Lock()
        self.evdev_config = evdev_config
        with open(policy_path, 'r') as file:
           json_data  = json.load(file)
           self.config = json_data["usb_hotplug_rules"]
        logger.debug("f{self.config}")
        self.vms = self.config.get("vms", {})
        self.blacklist = self.config.get("blacklist", [])
        self.whitelist = self.config.get("whitelist", [])
        self.class_rules = self.config.get("class_rules", {})
        self.vm_device_filter = self.config.get("device_filter", [])

    def update_policy(self, policy):
        with self.lock:
            self.config = policy
        self.vms = self.config.get("vms", [])
        self.blacklist = self.config.get("blacklist", [])
        self.whitelist = self.config.get("whitelist", [])
        self.class_rules = self.config.get("class_rules", {})
        self.vm_device_filter = self.config.get("device_filter", [])

    def vm_for_evdev_devices(self):
        if self.evdev_config is not None:
            vm_name, pciport_prefix = self.evdev_config.split(":", 1)
        vm = {}
        vm["name"] = vm_name
        vm["qmpSocket"] = self.vms[vm_name]

        return vm, pciport_prefix

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
                    if len(vms) > 1:
                        raise ValueError("More than one VM can access this device. Feature not supported.")
                    elif len(vms) == 1:
                        vm["name"] = vms[0]
                        vm["qmpSocket"] = self.vms[vms[0]]
                    return vm
            except Exception as e:
                    logger.error(f"Failed to find VM for USB device in the configuration file: {e}")
            return None

    def get_all_vms(self):
        vms = []
        for key, val in self.vms.items():
            vms.append( {"name": key, "qmpSocket":val} )
        return vms
    
    def lookup(self, whitelist: dict, key: any) -> list:
        return whitelist.get(key, [])

    def blacklisted(self, vendor_id: any, product_id: any) -> bool:
        blacklisted_products = self.blacklist.get(vendor_id)
        if blacklisted_products is not None:
            return product_id in blacklisted_products
        else:
            neg_vendor = f"~{vendor_id}"
            whitelisted_products = self.blacklist.get(neg_vendor)
            if whitelisted_products is not None:
                return product_id not in whitelisted_products
            else:
                return False
                
    def is_vm_filtered(self, vm_device_filter: dict, vm: any, device_key_0: any, device_key_1: any) -> bool:
        devices = vm_device_filter.get(vm, [])
        if device_key_0 in devices:
            return True

        if device_key_1 in devices:
            return True

        return False

    def filter_vms(self, vm_device_filter: dict, sorted_vms: list, key0: any, key1: any) -> list:
      filtered_vms = [
          vm for vm in sorted_vms
          if not self.is_vm_filtered(vm_device_filter, vm, key0, key1)
      ]

      return filtered_vms

    def get_allowed_vms(self, device_class: int, subclass: int, protocol: int, vendor_id: int, product_id: int):

        # Check if the device is blacklisted
        if self.blacklisted(vendor_id, product_id):
            return []

        # Check if the device is mapped to a specific VM
        device_key_0 = f"{vendor_id}:{product_id}"
        device_key_1 = f"{vendor_id}:*"
        wl_vms = self.lookup(self.whitelist, device_key_0) + self.lookup(self.whitelist, device_key_1)
        
        # Based on class, subclass, and protocol find list VMs which can access it 
        class_key_0 = f"{device_class}:{subclass}:{protocol}"
        class_key_1 = f"{device_class}:{subclass}:*"
        class_key_2 = f"{device_class}:*:{protocol}"
        class_key_3 = f"{device_class}:*:*"
        cl_01_vms = self.lookup(self.class_rules, class_key_0) + self.lookup(self.class_rules, class_key_1)
        cl_23_vms = self.lookup(self.class_rules, class_key_2) + self.lookup(self.class_rules, class_key_3)
        cl_vms = cl_01_vms + cl_23_vms

        # Merge VMs from all above rules
        arr_vms = wl_vms + cl_vms
        unique_vms_set = list(set(arr_vms))
        
        # Filter any VM if it is disabled by the VM
        allowed_vms = self.filter_vms(self.vm_device_filter, unique_vms_set, device_key_0, device_key_1)

        return allowed_vms


############TESTS###############
class UnitTest:
    def __init__(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        policy_json = os.path.join(current_dir, '../testdata/', 'usb_hotplug_rules.json')
        static_policy = os.path.join(current_dir, '../testdata/', 'config.json')
        self.policy = GhafPolicy(static_policy, "gui-vm:rp")
        with open(policy_json, 'r') as file:
            json_data = json.load(file)["result"]
            self.policy.update_policy(json_data["usb_hotplug_rules"])
        
    def print_vmlist(self):
        print("VM LIST:")
        print(self.policy.get_all_vms())
        res = self.policy.vm_for_evdev_devices()
        vm = res[0]
        prefix = res[1]
        print("\n EVDEV Passthrough:")
        print(f"evdev passthrough {vm['name']}:{vm['qmpSocket']} prefix: {prefix}\n\n")

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
        expected_vms=[]
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
        expected_vms=[]
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
        expected_vms=[]
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

