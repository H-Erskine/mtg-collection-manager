terraform {
  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 6.0"
    }
  }
}

provider "oci" {
  tenancy_ocid     = var.tenancy_ocid
  user_ocid        = var.user_ocid
  fingerprint      = var.fingerprint
  private_key_path = var.private_key_path
  region           = var.region
}

# ── Networking ────────────────────────────────────────────────────────────────

resource "oci_core_vcn" "mtg_vcn" {
  compartment_id = var.compartment_ocid
  cidr_block     = "10.0.0.0/16"
  display_name   = "mtg-vcn"
  dns_label      = "mtgvcn"
}

resource "oci_core_internet_gateway" "mtg_igw" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.mtg_vcn.id
  display_name   = "mtg-igw"
}

resource "oci_core_route_table" "mtg_rt" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.mtg_vcn.id
  display_name   = "mtg-rt"

  route_rules {
    destination       = "0.0.0.0/0"
    network_entity_id = oci_core_internet_gateway.mtg_igw.id
  }
}

resource "oci_core_security_list" "mtg_sl" {
  compartment_id = var.compartment_ocid
  vcn_id         = oci_core_vcn.mtg_vcn.id
  display_name   = "mtg-security-list"

  egress_security_rules {
    destination = "0.0.0.0/0"
    protocol    = "all"
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    protocol = "6"
    source   = "0.0.0.0/0"
    tcp_options {
      min = 443
      max = 443
    }
  }
}

resource "oci_core_subnet" "mtg_subnet" {
  compartment_id    = var.compartment_ocid
  vcn_id            = oci_core_vcn.mtg_vcn.id
  cidr_block        = "10.0.1.0/24"
  display_name      = "mtg-subnet"
  dns_label         = "mtgsubnet"
  route_table_id    = oci_core_route_table.mtg_rt.id
  security_list_ids = [oci_core_security_list.mtg_sl.id]
}

# ── Compute ───────────────────────────────────────────────────────────────────

resource "oci_core_instance" "mtg_vm" {
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "MTG-Manager-VM"
  shape               = "VM.Standard.E2.1.Micro"

  source_details {
    source_type = "image"
    source_id   = var.instance_image_ocid
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.mtg_subnet.id
    assign_public_ip = true
    display_name     = "mtg-vnic"
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
    user_data           = base64encode(file("${path.module}/cloud-init.yaml"))
  }
}
