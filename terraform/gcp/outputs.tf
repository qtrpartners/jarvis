# Every provider module emits the same output names. This is where the
# abstraction genuinely holds.

output "environment" {
  value = var.env
}

output "provider" {
  value = "gcp"
}

output "project_id" {
  value = var.project_id
}

output "configuration" {
  value = {
    region                       = var.region
    zone                         = var.zone
    db_availability_type         = var.db_availability_type
    db_deletion_protection       = var.db_deletion_protection
    workload_deletion_protection = var.workload_deletion_protection
    raw_coldline_days            = var.raw_coldline_after_days
    log_retention_days           = var.log_delete_after_days
    scratch_retention_days       = var.scratch_delete_after_days
    noncurrent_version_days      = var.noncurrent_version_delete_after_days
    notebook_snapshot_days       = var.notebook_snapshot_retention_days
    notebook_data_disk_size_gb   = var.notebook_data_disk_size_gb
    host_images                  = var.host_images
    host_replacement_role        = var.host_replacement_role
  }
}

output "host_image_contract" {
  description = "Immutable host inputs and replacement semantics for the three VM roles."
  value = {
    images              = var.host_images
    startup_scripts     = false
    build_manifest_path = "/etc/jarvis-host-image.json"
    baked_prerequisites = ["docker-ce", "docker-compose-plugin", "google-cloud-ops-agent", "jarvis-compose-supervisor", "jarvis-notebook-storage", "rsync", "os-hardening"]
    update_strategy     = "change one role image reference and replace that role through a reviewed Terraform plan"
    rollback_strategy   = "restore the prior exact image reference through a reviewed Terraform plan"
    replacement_role    = var.host_replacement_role
  }
}

output "service_supervision_contract" {
  description = "Host-level lifecycle, health, and bounded-restart contract for Compose roles."
  value = {
    roles                   = ["control", "feed", "notebook"]
    systemd_unit_template   = "jarvis-compose@.service"
    enabled_on_first_deploy = true
    boot_target             = "multi-user.target"
    compose_restart_policy  = "no"
    restart = {
      policy           = "on-failure"
      delay_seconds    = 20
      interval_seconds = 300
      burst            = 3
    }
    health = {
      command                      = "sudo /usr/local/sbin/jarvis-compose health ROLE"
      format                       = "json"
      interval_seconds             = 30
      consecutive_failures_allowed = 2
    }
  }
}

output "guardrails" {
  value = {
    enabled_services = sort(tolist(local.platform_services))
    labels           = local.common_labels
    audit_logging = {
      service   = google_project_iam_audit_config.all_services.service
      log_types = sort([for config in google_project_iam_audit_config.all_services.audit_log_config : config.log_type])
      exemptions = flatten([
        for config in google_project_iam_audit_config.all_services.audit_log_config :
        config.exempted_members == null ? [] : config.exempted_members
      ])
    }
    budget = {
      billing_account      = var.billing_account_id
      monthly_amount_usd   = var.monthly_budget_usd
      thresholds           = local.budget_thresholds
      notification_channel = google_monitoring_notification_channel.operations_email.name
    }
    quota_alerts = {
      warning_threshold = var.quota_warning_threshold
      utilization       = google_monitoring_alert_policy.quota_utilization.display_name
      exceeded          = google_monitoring_alert_policy.quota_exceeded.display_name
    }
    deletion_protection = {
      cloud_sql     = google_sql_database_instance.airflow.deletion_protection
      cloud_run_job = google_cloud_run_v2_job.research.deletion_protection
      compute = {
        control  = google_compute_instance.control.deletion_protection
        feed     = google_compute_instance.feed.deletion_protection
        notebook = google_compute_instance.notebook.deletion_protection
      }
      bucket_force_destroy = google_storage_bucket.data.force_destroy
      storage_force_destroy = {
        data         = google_storage_bucket.data.force_destroy
        airflow_logs = google_storage_bucket.airflow_logs.force_destroy
        scratch      = google_storage_bucket.scratch.force_destroy
        backup       = google_storage_bucket.backup.force_destroy
      }
    }
    shielded_compute = {
      for name, instance in {
        control  = google_compute_instance.control
        feed     = google_compute_instance.feed
        notebook = google_compute_instance.notebook
        } : name => {
        secure_boot          = instance.shielded_instance_config[0].enable_secure_boot
        vtpm                 = instance.shielded_instance_config[0].enable_vtpm
        integrity_monitoring = instance.shielded_instance_config[0].enable_integrity_monitoring
      }
    }
    organization_policies = {
      required   = sort(tolist(local.required_organization_policies))
      optional   = sort(tolist(local.optional_organization_policies))
      managed_by = "organization landing-zone administrators outside this module"
    }
    external_iam = {
      principal = google_service_account.roles["deployer"].email
      scope     = "billingAccounts/${var.billing_account_id}"
      role      = "roles/billing.costsManager"
    }
  }
}

output "data_access_contract" {
  value = {
    default_deny = length(var.shared_storage_buckets) == 0 && length(var.shared_bigquery_datasets) == 0
    storage = {
      for bucket, config in var.shared_storage_buckets : bucket => {
        project_id         = config.project_id
        source_environment = config.source_environment
        location           = config.location
        owner              = config.owner
        classification     = config.classification
        approval_id        = config.approval_id
        review_on          = config.review_on
        grants = {
          for workload, access in config.workload_access : workload => {
            access = access
            role   = google_storage_bucket_iam_member.shared_data["${bucket}/${workload}"].role
            member = google_storage_bucket_iam_member.shared_data["${bucket}/${workload}"].member
          }
        }
      }
    }
    bigquery = {
      for alias, config in var.shared_bigquery_datasets : alias => {
        project_id         = config.project_id
        dataset_id         = config.dataset_id
        source_environment = config.source_environment
        location           = config.location
        owner              = config.owner
        classification     = config.classification
        approval_id        = config.approval_id
        review_on          = config.review_on
        grants = {
          for workload, access in config.workload_access : workload => {
            access = access
            role   = google_bigquery_dataset_iam_member.shared_data["${alias}/${workload}"].role
            member = google_bigquery_dataset_iam_member.shared_data["${alias}/${workload}"].member
          }
        }
      }
    }
    bigquery_job_users = {
      for workload, binding in google_project_iam_member.shared_bigquery_job_user :
      workload => binding.member
    }
    prohibited_principals = ["control", "ci", "deployer", "operators"]
    policy_owner          = "source data owner approves; Jarvis Terraform manages only declared member grants"
  }
}

output "storage_uri" {
  value = "gs://${google_storage_bucket.data.name}"
}

output "airflow_logs_uri" {
  value = "gs://${google_storage_bucket.airflow_logs.name}"
}

output "scratch_uri" {
  value = "gs://${google_storage_bucket.scratch.name}"
}

output "backup_uri" {
  value = "gs://${google_storage_bucket.backup.name}"
}

output "storage_locations" {
  value = {
    data         = "gs://${google_storage_bucket.data.name}"
    airflow_logs = "gs://${google_storage_bucket.airflow_logs.name}"
    scratch      = "gs://${google_storage_bucket.scratch.name}"
    backup       = "gs://${google_storage_bucket.backup.name}"
  }
}

output "storage_lifecycle_policy" {
  description = "Approved storage lifecycle policy and provider enforcement semantics."
  value       = local.storage_lifecycle_policy
}

output "storage_contract" {
  value = {
    data = {
      uri               = "gs://${google_storage_bucket.data.name}"
      versioning        = google_storage_bucket.data.versioning[0].enabled
      public_prevention = google_storage_bucket.data.public_access_prevention
      workload_access = {
        job      = "writer"
        feed     = "creator"
        notebook = "reader"
      }
      workload_roles = {
        job      = google_storage_bucket_iam_member.job_data.role
        feed     = google_storage_bucket_iam_member.feed_write.role
        notebook = google_storage_bucket_iam_member.notebook_read.role
      }
    }
    airflow_logs = {
      uri               = "gs://${google_storage_bucket.airflow_logs.name}"
      versioning        = google_storage_bucket.airflow_logs.versioning[0].enabled
      public_prevention = google_storage_bucket.airflow_logs.public_access_prevention
      workload_access = {
        control = "writer"
      }
      workload_roles = {
        control = google_storage_bucket_iam_member.control_logs.role
      }
    }
    scratch = {
      uri               = "gs://${google_storage_bucket.scratch.name}"
      versioning        = google_storage_bucket.scratch.versioning[0].enabled
      public_prevention = google_storage_bucket.scratch.public_access_prevention
      workload_access = {
        job      = "writer"
        notebook = "writer"
      }
      workload_roles = {
        job      = google_storage_bucket_iam_member.job_scratch.role
        notebook = google_storage_bucket_iam_member.notebook_scratch.role
      }
    }
    backup = {
      uri               = "gs://${google_storage_bucket.backup.name}"
      versioning        = google_storage_bucket.backup.versioning[0].enabled
      public_prevention = google_storage_bucket.backup.public_access_prevention
      workload_access   = {}
      workload_roles    = {}
    }
  }
}

output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "image_repository" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/base"
}

output "batch_job_name" {
  value = google_cloud_run_v2_job.research.name
}

output "db_host" {
  value = google_sql_database_instance.airflow.private_ip_address
}

output "db_connection_name" {
  value = google_sql_database_instance.airflow.connection_name
}

output "database_policy" {
  description = "Effective Cloud SQL availability, recovery, maintenance, and observability contract."
  value = {
    engine            = google_sql_database_instance.airflow.database_version
    tier              = google_sql_database_instance.airflow.settings[0].tier
    availability_type = google_sql_database_instance.airflow.settings[0].availability_type
    connectivity = {
      public_ipv4     = google_sql_database_instance.airflow.settings[0].ip_configuration[0].ipv4_enabled
      private_network = google_sql_database_instance.airflow.settings[0].ip_configuration[0].private_network
      ssl_mode        = google_sql_database_instance.airflow.settings[0].ip_configuration[0].ssl_mode
    }
    storage = {
      type       = google_sql_database_instance.airflow.settings[0].disk_type
      autoresize = google_sql_database_instance.airflow.settings[0].disk_autoresize
    }
    backups = {
      enabled        = google_sql_database_instance.airflow.settings[0].backup_configuration[0].enabled
      start_time_utc = google_sql_database_instance.airflow.settings[0].backup_configuration[0].start_time
      retained_count = google_sql_database_instance.airflow.settings[0].backup_configuration[0].backup_retention_settings[0].retained_backups
      retention_unit = google_sql_database_instance.airflow.settings[0].backup_configuration[0].backup_retention_settings[0].retention_unit
    }
    point_in_time_recovery = {
      enabled                        = google_sql_database_instance.airflow.settings[0].backup_configuration[0].point_in_time_recovery_enabled
      transaction_log_retention_days = google_sql_database_instance.airflow.settings[0].backup_configuration[0].transaction_log_retention_days
    }
    maintenance = {
      day_utc      = google_sql_database_instance.airflow.settings[0].maintenance_window[0].day
      hour_utc     = google_sql_database_instance.airflow.settings[0].maintenance_window[0].hour
      update_track = google_sql_database_instance.airflow.settings[0].maintenance_window[0].update_track
    }
    query_insights = {
      enabled                 = google_sql_database_instance.airflow.settings[0].insights_config[0].query_insights_enabled
      plans_per_minute        = google_sql_database_instance.airflow.settings[0].insights_config[0].query_plans_per_minute
      query_string_length     = google_sql_database_instance.airflow.settings[0].insights_config[0].query_string_length
      record_application_tags = google_sql_database_instance.airflow.settings[0].insights_config[0].record_application_tags
      record_client_address   = google_sql_database_instance.airflow.settings[0].insights_config[0].record_client_address
    }
    deletion_protection = {
      terraform = google_sql_database_instance.airflow.deletion_protection
      api       = google_sql_database_instance.airflow.settings[0].deletion_protection_enabled
    }
    recovery_objectives = {
      rpo_minutes = 5
      rto_minutes = 120
      status      = "p2.5-automation-ready-live-evidence-required"
    }
  }
}

output "identities" {
  value = { for k, v in google_service_account.roles : k => v.email }
}

output "github_oidc" {
  value = {
    workload_identity_provider  = google_iam_workload_identity_pool_provider.github.name
    ci_service_account          = google_service_account.roles["ci"].email
    recovery_service_account    = google_service_account.roles["recovery"].email
    integration_service_account = google_service_account.roles["integration"].email
    repository                  = var.github_repository
    repository_id               = var.github_repository_id
    repository_owner_id         = var.github_repository_owner_id
    environment                 = var.github_environment
    ref                         = var.github_ref
    attribute_condition         = google_iam_workload_identity_pool_provider.github.attribute_condition
  }
}

output "iam_contract" {
  value = {
    deployer_project_roles = sort(tolist(local.deployer_project_roles))
    deployer_iap_condition = google_project_iam_member.deployer_iap_ssh.condition[0].expression
    deployer_principals    = sort(tolist(var.deployer_principals))
    operator_principals    = sort(tolist(var.operator_principals))
    operator_access = {
      project_roles = sort([
        google_project_iam_member.operator_os_login[sort(tolist(var.operator_principals))[0]].role,
        google_project_iam_member.operator_iap_ssh[sort(tolist(var.operator_principals))[0]].role,
        google_project_iam_custom_role.instance_power_operator.name,
      ])
      service_accounts = sort(tolist(local.ssh_service_accounts))
      iap_condition    = google_project_iam_member.operator_iap_ssh[sort(tolist(var.operator_principals))[0]].condition[0].expression
    }
    runtime_project_roles = {
      control = sort(concat(
        [google_project_iam_member.control_sql_client.role],
        tolist(local.vm_observability_roles),
      ))
      job = contains(local.bigquery_job_workloads, "job") ? [
        google_project_iam_member.shared_bigquery_job_user["job"].role,
      ] : []
      feed = sort(tolist(local.vm_observability_roles))
      notebook = sort(concat(
        tolist(local.vm_observability_roles),
        contains(local.bigquery_job_workloads, "notebook") ? [
          google_project_iam_member.shared_bigquery_job_user["notebook"].role,
        ] : [],
      ))
      ci          = []
      recovery    = var.env == "prod" ? [] : sort(tolist(local.recovery_project_roles))
      integration = var.env == "stage" ? [google_project_iam_member.integration_cloud_run[0].role] : []
    }
    resource_roles = {
      control = sort([
        google_cloud_run_v2_job_iam_member.control_executor.role,
        google_storage_bucket_iam_member.control_logs.role,
        google_secret_manager_secret_iam_member.control_airflow_config["database"].role,
        google_secret_manager_secret_iam_member.control_airflow_config["fernet"].role,
        google_artifact_registry_repository_iam_member.runtime_readers["control"].role,
      ])
      job = sort([
        google_storage_bucket_iam_member.job_data.role,
        google_storage_bucket_iam_member.job_scratch.role,
        google_secret_manager_secret_iam_member.job_vendor_credentials.role,
        google_artifact_registry_repository_iam_member.runtime_readers["job"].role,
      ])
      feed = sort([
        google_storage_bucket_iam_member.feed_write.role,
        google_secret_manager_secret_iam_member.feed_credentials.role,
        google_artifact_registry_repository_iam_member.runtime_readers["feed"].role,
      ])
      notebook = sort([
        google_storage_bucket_iam_member.notebook_read.role,
        google_storage_bucket_iam_member.notebook_scratch.role,
        google_artifact_registry_repository_iam_member.runtime_readers["notebook"].role,
      ])
      ci = [google_artifact_registry_repository_iam_member.ci_writer.role]
      recovery = var.env == "prod" ? [] : sort([
        google_storage_bucket_iam_member.recovery_data_canary[0].role,
        google_storage_bucket_iam_member.recovery_evidence[0].role,
      ])
      integration = var.env == "stage" ? sort([
        google_storage_bucket_iam_member.integration_data[0].role,
        google_storage_bucket_iam_member.integration_logs[0].role,
      ]) : []
    }
    workload_attachments = {
      control  = google_compute_instance.control.service_account[0].email
      feed     = google_compute_instance.feed.service_account[0].email
      notebook = google_compute_instance.notebook.service_account[0].email
      job      = google_cloud_run_v2_job.research.template[0].template[0].service_account
    }
  }
}

output "instances" {
  value = {
    control  = google_compute_instance.control.name
    feed     = google_compute_instance.feed.name
    notebook = google_compute_instance.notebook.name
  }
}

output "notebook_storage" {
  description = "Durable notebook filesystem and recovery contract."
  value = {
    mode                      = "gcp-pd"
    disk_name                 = google_compute_disk.notebooks.name
    device_name               = "jarvis-notebooks"
    host_mount_path           = "/mnt/jarvis-notebooks"
    encrypted                 = true
    encryption                = "google-managed-at-rest"
    filesystem                = "ext4"
    snapshot_policy           = google_compute_resource_policy.notebook_snapshots.name
    snapshot_retention_days   = var.notebook_snapshot_retention_days
    survives_host_replacement = true
  }
}

output "private_ips" {
  value = {
    control  = google_compute_instance.control.network_interface[0].network_ip
    feed     = google_compute_instance.feed.network_interface[0].network_ip
    notebook = google_compute_instance.notebook.network_interface[0].network_ip
  }
}

output "compute_networking" {
  value = {
    external_access_config_count = {
      control  = length(google_compute_instance.control.network_interface[0].access_config)
      feed     = length(google_compute_instance.feed.network_interface[0].access_config)
      notebook = length(google_compute_instance.notebook.network_interface[0].access_config)
    }
    os_login_enabled         = google_compute_instance.control.metadata["enable-oslogin"] == "TRUE"
    project_ssh_keys_blocked = google_compute_instance.control.metadata["block-project-ssh-keys"] == "TRUE"
  }
}

output "airflow_secrets_backend" {
  value = "airflow.providers.google.cloud.secrets.secret_manager.CloudSecretManagerBackend"
}

output "runtime_secret_contract" {
  description = "Non-secret identifiers and the exact workload allowed to resolve each value."
  value = {
    airflow_sql_alchemy_conn = {
      secret_id = google_secret_manager_secret.airflow_sql_alchemy_conn.secret_id
      consumer  = "control"
      config    = "sql_alchemy_conn"
    }
    airflow_fernet_key = {
      secret_id = google_secret_manager_secret.airflow_fernet_key.secret_id
      consumer  = "control"
      config    = "fernet_key"
    }
    vendor_credentials = {
      secret_id = google_secret_manager_secret.vendor_credentials.secret_id
      consumer  = "job"
      env_ref   = "RP_VENDOR_CREDENTIAL_SECRET_ID"
    }
    feed_credentials = {
      secret_id = google_secret_manager_secret.feed_credentials.secret_id
      consumer  = "feed"
      env_ref   = "RP_FEED_CREDENTIAL_SECRET_ID"
    }
  }
}

output "recovery_contract" {
  description = "Quarterly recovery-drill identity, backup controls, and safety boundary."
  value = {
    enabled                   = var.env != "prod"
    environment               = var.env
    service_account           = google_service_account.roles["recovery"].email
    production_access         = false
    data_canary_prefix        = "gs://${google_storage_bucket.data.name}/recovery-drills/"
    evidence_prefix           = "gs://${google_storage_bucket.backup.name}/recovery-drills/"
    notebook_snapshot_policy  = google_compute_resource_policy.notebook_snapshots.name
    notebook_source_disk      = google_compute_disk.notebooks.name
    snapshot_retention_days   = var.notebook_snapshot_retention_days
    source_disk_delete_policy = google_compute_resource_policy.notebook_snapshots.snapshot_schedule_policy[0].retention_policy[0].on_source_disk_delete
    objectives = {
      rpo_seconds = 300
      rto_seconds = 7200
    }
    state_bucket_access = "grant this service account as a bootstrap state_recovery_principal"
  }
}
