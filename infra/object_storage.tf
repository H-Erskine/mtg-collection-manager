# ── Object Storage backup bucket ──────────────────────────────────────────────

resource "oci_objectstorage_bucket" "mtg_backup" {
  compartment_id = var.compartment_ocid
  namespace      = data.oci_objectstorage_namespace.ns.namespace
  name           = var.backup_bucket_name
  access_type    = "NoPublicAccess"

  retention_rules {
    display_name = "7-day retention"
    duration {
      time_amount = 7
      time_unit   = "DAYS"
    }
  }
}

data "oci_objectstorage_namespace" "ns" {
  compartment_id = var.compartment_ocid
}

# ── Instance principal IAM (lets the VM write backups without stored keys) ────

resource "oci_identity_dynamic_group" "mtg_vm_dg" {
  compartment_id = var.tenancy_ocid
  name           = "mtg-vm-dynamic-group"
  description    = "MTG Manager VM instance principal group"
  matching_rule  = "Any {instance.compartment.id = '${var.compartment_ocid}'}"
}

resource "oci_identity_policy" "mtg_backup_policy" {
  compartment_id = var.tenancy_ocid
  name           = "mtg-backup-policy"
  description    = "Allow MTG Manager VM to write DB backups to Object Storage"
  statements = [
    "Allow dynamic-group ${oci_identity_dynamic_group.mtg_vm_dg.name} to manage objects in tenancy where target.bucket.name='${var.backup_bucket_name}'",
  ]
}
