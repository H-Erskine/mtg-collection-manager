variable "tenancy_ocid" {
  default = "ocid1.tenancy.oc1..aaaaaaaaiwvgsx7fuwg3qqhjiadersjxmhteicp3vqnonna7revlb3neq3ba"
}

variable "user_ocid" {
  default = "ocid1.user.oc1..aaaaaaaa5ii5bffzz3zukoi2p7q4hpqb23hx3cxpm4meiqv72owdeuznb6za"
}

variable "fingerprint" {
  default = "f0:35:73:37:16:7d:4d:99:77:e9:5d:38:56:d7:c1:f3"
}

variable "private_key_path" {
  default = "~/.oci/oci_api_key.pem"
}

variable "region" {
  default = "uk-london-1"
}

variable "compartment_ocid" {
  default = "ocid1.tenancy.oc1..aaaaaaaaiwvgsx7fuwg3qqhjiadersjxmhteicp3vqnonna7revlb3neq3ba"
}

variable "availability_domain" {
  default = "ijra:UK-LONDON-1-AD-3"
}

variable "instance_image_ocid" {
  # Ubuntu 22.04 x86 (E2.1.Micro) - uk-london-1
  default = "ocid1.image.oc1.uk-london-1.aaaaaaaatydypej2moftykfmeaon43pzm4ifqq6rgivcoclp6tqn4qaoo75a"
}

variable "ssh_public_key" {
  default = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQChCN8BCNbwQqV2k76FdWYOXlFjTc0xjqlOL3255TWA+E3+wwFUeQDES3wegQyoerMLlFt5r6UhwHCnLvd6y/BTQeXz26jL/O8J6D+V9ZWXz2NsPh8eL+zUJNVJAhio1Unaz9DPSrR7KA80gb+Z6cqpyr/DaXl+2FW42bKeKucTaGH4bc+oMJU0izAVvk8ToPXs8jJcv7fh6SmqlmUfmyNy7R99/xl8ykoNhJlA8Zcc69KngpIWm90IQ6bvNigf9TT/fDHmI2g4WzQAfdMij38SGGaEUUcZdTBdmJfKFUCgThsxHDcV2KEJ0ECUtxdHVFeZVWg/90gj+FqrqwrRXxAp"
}

variable "backup_bucket_name" {
  default = "mtg-db-backup"
}
