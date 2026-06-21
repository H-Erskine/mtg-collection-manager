output "vm_public_ip" {
  description = "Public IP of the MTG Manager VM — update GitHub Actions secret VM_HOST with this value"
  value       = oci_core_instance.mtg_vm.public_ip
}

output "backup_bucket" {
  description = "Object Storage bucket name for DB backups"
  value       = oci_objectstorage_bucket.mtg_backup.name
}

output "object_storage_namespace" {
  description = "Object Storage namespace — needed in backup_db.sh"
  value       = data.oci_objectstorage_namespace.ns.namespace
}
