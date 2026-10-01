mock_provider "google" {
  mock_resource "google_service_account" {
    defaults = {
      email = "mock-identity@jarvis-research-dev.iam.gserviceaccount.com"
      name  = "projects/jarvis-research-dev/serviceAccounts/mock-identity@jarvis-research-dev.iam.gserviceaccount.com"
    }
  }

  mock_resource "google_iam_workload_identity_pool" {
    defaults = {
      name = "projects/1234567890/locations/global/workloadIdentityPools/research-dev-github"
    }
  }

  mock_resource "google_project_iam_custom_role" {
    defaults = {
      name = "projects/jarvis-research-dev/roles/jarvisInstancePower"
    }
  }

  mock_resource "google_monitoring_notification_channel" {
    defaults = {
      name = "projects/jarvis-research-dev/notificationChannels/123456789"
    }
  }

  mock_data "google_project" {
    defaults = {
      number          = "1234567890"
      billing_account = "000000-000000-000000"
    }
  }
}
variables {
  project_id                 = "jarvis-research-dev"
  env                        = "dev"
  bucket_name                = "jarvis-research-dev-data"
  airflow_log_bucket_name    = "jarvis-research-dev-airflow-logs"
  scratch_bucket_name        = "jarvis-research-dev-scratch"
  backup_bucket_name         = "jarvis-research-dev-backup"
  billing_account_id         = "000000-000000-000000"
  alert_email                = "operations@example.com"
  monthly_budget_usd         = 500
  labels                     = { owner = "platform", cost_center = "research" }
  network_self_link          = "projects/jarvis-research-dev/global/networks/research-dev-vpc"
  subnetwork_self_link       = "projects/jarvis-research-dev/regions/us-central1/subnetworks/research-dev-workloads"
  deployer_principals        = ["group:platform@example.com"]
  operator_principals        = ["group:operators@example.com"]
  github_repository          = "example/jarvis"
  github_repository_id       = "123456789"
  github_repository_owner_id = "987654321"
  github_environment         = "development"
  host_images = {
    control  = "projects/jarvis-research-dev/global/images/jarvis-host-0123456789ab-202609170000"
    feed     = "projects/jarvis-research-dev/global/images/jarvis-host-0123456789ab-202609170000"
    notebook = "projects/jarvis-research-dev/global/images/jarvis-host-0123456789ab-202609170000"
  }
}

run "least_privilege_identity_contract" {
  command = apply

  assert {
    condition     = toset(keys(google_service_account.roles)) == toset(["deployer", "ci", "recovery", "integration", "control", "job", "feed", "notebook"])
    error_message = "Every automation and runtime role must have a separate user-managed service account."
  }

  assert {
    condition     = google_sql_database_instance.airflow.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY"
    error_message = "Cloud SQL must reject unencrypted client connections."
  }

  assert {
    condition = (
      !google_sql_database_instance.airflow.settings[0].ip_configuration[0].ipv4_enabled &&
      google_sql_database_instance.airflow.settings[0].disk_autoresize &&
      google_sql_database_instance.airflow.settings[0].disk_type == "PD_SSD" &&
      google_sql_database_instance.airflow.settings[0].backup_configuration[0].enabled &&
      google_sql_database_instance.airflow.settings[0].backup_configuration[0].point_in_time_recovery_enabled &&
      google_sql_database_instance.airflow.settings[0].backup_configuration[0].transaction_log_retention_days == 7 &&
      google_sql_database_instance.airflow.settings[0].backup_configuration[0].backup_retention_settings[0].retained_backups == 8 &&
      google_sql_database_instance.airflow.settings[0].backup_configuration[0].backup_retention_settings[0].retention_unit == "COUNT" &&
      google_sql_database_instance.airflow.settings[0].maintenance_window[0].day == 7 &&
      google_sql_database_instance.airflow.settings[0].maintenance_window[0].hour == 8 &&
      google_sql_database_instance.airflow.settings[0].maintenance_window[0].update_track == "stable" &&
      google_sql_database_instance.airflow.settings[0].insights_config[0].query_insights_enabled &&
      google_sql_database_instance.airflow.settings[0].insights_config[0].query_plans_per_minute == 5 &&
      google_sql_database_instance.airflow.settings[0].insights_config[0].query_string_length == 1024 &&
      google_sql_database_instance.airflow.settings[0].insights_config[0].record_application_tags &&
      !google_sql_database_instance.airflow.settings[0].insights_config[0].record_client_address &&
      google_sql_database_instance.airflow.deletion_protection &&
      google_sql_database_instance.airflow.settings[0].deletion_protection_enabled
    )
    error_message = "Cloud SQL must enforce private access, storage growth, backups, PITR, maintenance, insights, and deletion safeguards."
  }

  assert {
    condition = (
      google_compute_instance.control.service_account[0].email == google_service_account.roles["control"].email &&
      google_compute_instance.feed.service_account[0].email == google_service_account.roles["feed"].email &&
      google_compute_instance.notebook.service_account[0].email == google_service_account.roles["notebook"].email &&
      google_cloud_run_v2_job.research.template[0].template[0].service_account == google_service_account.roles["job"].email
    )
    error_message = "Every workload must attach its dedicated service account instead of a default identity."
  }

  assert {
    condition     = toset([for binding in google_project_iam_member.deployer : binding.role]) == local.deployer_project_roles
    error_message = "The deployer must receive exactly the reviewed infrastructure administration roles."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.deployer_act_as) == 4 &&
      alltrue([
        for name, binding in google_service_account_iam_member.deployer_act_as :
        binding.role == "roles/iam.serviceAccountUser" &&
        binding.service_account_id == google_service_account.roles[name].name
      ])
    )
    error_message = "The deployer may act as each runtime identity, but no automation identity."
  }

  assert {
    condition = (
      google_project_iam_member.deployer_iap_ssh.role == "roles/iap.tunnelResourceAccessor" &&
      google_project_iam_member.deployer_iap_ssh.condition[0].expression == "destination.port == 22" &&
      length(google_project_iam_member.vm_observability) == 6 &&
      alltrue([
        for binding in google_project_iam_member.vm_observability :
        contains(["roles/logging.logWriter", "roles/monitoring.metricWriter"], binding.role)
      ])
    )
    error_message = "Private image builds and VM telemetry must use the reviewed IAP and observability grants."
  }

  assert {
    condition = (
      google_compute_instance.control.boot_disk[0].initialize_params[0].image == var.host_images.control &&
      google_compute_instance.feed.boot_disk[0].initialize_params[0].image == var.host_images.feed &&
      google_compute_instance.notebook.boot_disk[0].initialize_params[0].image == var.host_images.notebook &&
      output.host_image_contract.startup_scripts == false &&
      output.host_image_contract.build_manifest_path == "/etc/jarvis-host-image.json" &&
      output.service_supervision_contract.systemd_unit_template == "jarvis-compose@.service" &&
      output.service_supervision_contract.compose_restart_policy == "no" &&
      output.service_supervision_contract.restart.policy == "on-failure" &&
      output.service_supervision_contract.restart.burst == 3 &&
      output.service_supervision_contract.health.format == "json"
    )
    error_message = "Every VM role must boot from its immutable image and expose the bounded systemd supervision contract."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.deployer_impersonators) == 1 &&
      alltrue([
        for binding in google_service_account_iam_member.deployer_impersonators :
        binding.role == "roles/iam.serviceAccountTokenCreator" &&
        binding.service_account_id == google_service_account.roles["deployer"].name
      ])
    )
    error_message = "Named deployer principals may mint short-lived credentials only through the deployer service account."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.recovery_workload_identity) == 1 &&
      google_service_account_iam_member.recovery_workload_identity[0].role == "roles/iam.workloadIdentityUser" &&
      toset([for binding in google_project_iam_member.recovery : binding.role]) == local.recovery_project_roles &&
      google_project_iam_member.recovery["roles/iap.tunnelResourceAccessor"].condition[0].expression == "destination.port == 22" &&
      google_service_account_iam_member.recovery_act_as_control[0].service_account_id == google_service_account.roles["control"].name &&
      google_service_account_iam_member.recovery_act_as_control[0].role == "roles/iam.serviceAccountUser"
    )
    error_message = "Non-production recovery automation must use its own OIDC identity and may act only as control."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.integration_workload_identity) == 0 &&
      length(google_project_iam_custom_role.staging_integration) == 0 &&
      length(google_project_iam_member.integration_cloud_run) == 0 &&
      length(google_service_account_iam_member.integration_act_as_job) == 0 &&
      length(google_storage_bucket_iam_member.integration_data) == 0 &&
      length(google_project_iam_custom_role.staging_integration_logs) == 0 &&
      length(google_storage_bucket_iam_member.integration_logs) == 0 &&
      length(output.iam_contract.runtime_project_roles.integration) == 0 &&
      length(output.iam_contract.resource_roles.integration) == 0
    )
    error_message = "Staging integration automation must receive no development permissions."
  }

  assert {
    condition = (
      length(google_project_iam_member.operator_os_login) == 1 &&
      alltrue([for binding in google_project_iam_member.operator_os_login : binding.role == "roles/compute.osAdminLogin"]) &&
      length(google_project_iam_member.operator_iap_ssh) == 1 &&
      alltrue([
        for binding in google_project_iam_member.operator_iap_ssh :
        binding.role == "roles/iap.tunnelResourceAccessor" &&
        binding.condition[0].expression == "destination.port == 22"
      ]) &&
      length(google_project_iam_member.operator_instance_power) == 1 &&
      alltrue([
        for binding in google_project_iam_member.operator_instance_power :
        binding.role == google_project_iam_custom_role.instance_power_operator.name
      ])
    )
    error_message = "Operators must be limited to OS Admin Login, port-22 IAP tunnels, and instance start/stop."
  }

  assert {
    condition = (
      length(google_service_account_iam_member.operator_act_as) == 3 &&
      alltrue([
        for binding in google_service_account_iam_member.operator_act_as :
        binding.role == "roles/iam.serviceAccountUser"
      ])
    )
    error_message = "Operators may act as the three VM identities, but not batch, CI, or deployer identities."
  }

  assert {
    condition = (
      google_cloud_run_v2_job_iam_member.control_executor.role == "roles/run.jobsExecutorWithOverrides" &&
      google_project_iam_member.control_sql_client.role == "roles/cloudsql.client" &&
      google_storage_bucket_iam_member.control_logs.role == "roles/storage.objectAdmin" &&
      google_storage_bucket_iam_member.control_logs.bucket == google_storage_bucket.airflow_logs.name &&
      length(google_secret_manager_secret_iam_member.control_airflow_config) == 2 &&
      alltrue([
        for binding in google_secret_manager_secret_iam_member.control_airflow_config :
        binding.role == "roles/secretmanager.secretAccessor"
      ])
    )
    error_message = "The control role must be limited to job execution, metadata database access, logs, and its Airflow config secrets."
  }

  assert {
    condition = (
      google_secret_manager_secret_iam_member.job_vendor_credentials.secret_id == google_secret_manager_secret.vendor_credentials.id &&
      google_secret_manager_secret_iam_member.job_vendor_credentials.role == "roles/secretmanager.secretAccessor" &&
      google_secret_manager_secret_iam_member.feed_credentials.secret_id == google_secret_manager_secret.feed_credentials.id &&
      google_secret_manager_secret_iam_member.feed_credentials.role == "roles/secretmanager.secretAccessor" &&
      output.runtime_secret_contract.vendor_credentials.consumer == "job" &&
      output.runtime_secret_contract.feed_credentials.consumer == "feed"
    )
    error_message = "Vendor and feed credentials must be isolated to their owning workload identity."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.job_data.role == "roles/storage.objectAdmin" &&
      google_storage_bucket_iam_member.job_data.bucket == google_storage_bucket.data.name &&
      google_storage_bucket_iam_member.feed_write.role == "roles/storage.objectCreator" &&
      google_storage_bucket_iam_member.feed_write.bucket == google_storage_bucket.data.name &&
      google_storage_bucket_iam_member.notebook_read.role == "roles/storage.objectViewer" &&
      google_storage_bucket_iam_member.notebook_read.bucket == google_storage_bucket.data.name &&
      google_storage_bucket_iam_member.job_scratch.role == "roles/storage.objectAdmin" &&
      google_storage_bucket_iam_member.job_scratch.bucket == google_storage_bucket.scratch.name &&
      google_storage_bucket_iam_member.notebook_scratch.role == "roles/storage.objectAdmin" &&
      google_storage_bucket_iam_member.notebook_scratch.bucket == google_storage_bucket.scratch.name
    )
    error_message = "Job, feed, and notebook storage privileges must remain distinct."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.recovery_data_canary[0].bucket == google_storage_bucket.data.name &&
      google_storage_bucket_iam_member.recovery_data_canary[0].role == "roles/storage.objectAdmin" &&
      strcontains(google_storage_bucket_iam_member.recovery_data_canary[0].condition[0].expression, "/objects/recovery-drills/") &&
      google_storage_bucket_iam_member.recovery_evidence[0].bucket == google_storage_bucket.backup.name &&
      google_storage_bucket_iam_member.recovery_evidence[0].role == "roles/storage.objectCreator"
    )
    error_message = "Recovery automation may mutate only data canaries and append drill evidence."
  }

  assert {
    condition = (
      google_compute_disk.notebooks.type == "pd-balanced" &&
      google_compute_disk.notebooks.size == 200 &&
      google_compute_instance.notebook.attached_disk[0].source == google_compute_disk.notebooks.self_link &&
      google_compute_instance.notebook.attached_disk[0].device_name == "jarvis-notebooks" &&
      google_compute_resource_policy.notebook_snapshots.snapshot_schedule_policy[0].schedule[0].daily_schedule[0].days_in_cycle == 1 &&
      google_compute_resource_policy.notebook_snapshots.snapshot_schedule_policy[0].retention_policy[0].max_retention_days == 14 &&
      google_compute_resource_policy.notebook_snapshots.snapshot_schedule_policy[0].retention_policy[0].on_source_disk_delete == "KEEP_AUTO_SNAPSHOTS" &&
      google_compute_disk_resource_policy_attachment.notebook_snapshots.name == google_compute_resource_policy.notebook_snapshots.name &&
      google_compute_disk_resource_policy_attachment.notebook_snapshots.disk == google_compute_disk.notebooks.name &&
      output.notebook_storage.mode == "gcp-pd" &&
      output.notebook_storage.survives_host_replacement &&
      output.recovery_contract.objectives.rpo_seconds == 300 &&
      output.recovery_contract.objectives.rto_seconds == 7200
    )
    error_message = "Notebook data must remain independent, encrypted, snapshotted, and testable."
  }

  assert {
    condition = (
      toset(keys(output.storage_locations)) == toset(["data", "airflow_logs", "scratch", "backup"]) &&
      output.storage_contract.data.versioning &&
      !output.storage_contract.airflow_logs.versioning &&
      !output.storage_contract.scratch.versioning &&
      output.storage_contract.backup.versioning &&
      length(output.storage_contract.backup.workload_roles) == 0 &&
      toset(keys(output.storage_contract.data.workload_roles)) == toset(["job", "feed", "notebook"]) &&
      toset(keys(output.storage_contract.airflow_logs.workload_roles)) == toset(["control"]) &&
      toset(keys(output.storage_contract.scratch.workload_roles)) == toset(["job", "notebook"])
    )
    error_message = "Storage locations must be physically distinct and expose only their intended workload roles."
  }

  assert {
    condition = (
      output.storage_lifecycle_policy.policy_version == "1" &&
      output.storage_lifecycle_policy.raw.transition_after_days == 90 &&
      !output.storage_lifecycle_policy.raw.delete_current &&
      output.storage_lifecycle_policy.noncurrent_data_versions.retained_count == 3 &&
      output.storage_lifecycle_policy.noncurrent_data_versions.minimum_age_days == 30 &&
      output.storage_lifecycle_policy.airflow_logs.delete_after_days == 90 &&
      output.storage_lifecycle_policy.scratch.delete_after_days == 14 &&
      output.storage_lifecycle_policy.backup.delete_after_days == null &&
      one([
        for rule in google_storage_bucket.scratch.lifecycle_rule : one(rule.condition).age
        if one(rule.action).type == "Delete"
      ]) == 14 &&
      one([
        for rule in google_storage_bucket.data.lifecycle_rule : one(rule.condition).num_newer_versions
        if one(rule.action).type == "Delete"
      ]) == 3 &&
      one([
        for rule in google_storage_bucket.data.lifecycle_rule : one(rule.condition).with_state
        if one(rule.action).type == "Delete"
      ]) == "ARCHIVED"
    )
    error_message = "GCP must enforce the approved raw, version, log, scratch, and backup lifecycle policy."
  }

  assert {
    condition = (
      google_artifact_registry_repository_iam_member.ci_writer.role == "roles/artifactregistry.writer" &&
      alltrue([
        for binding in google_artifact_registry_repository_iam_member.runtime_readers :
        binding.role == "roles/artifactregistry.reader"
      ])
    )
    error_message = "CI may publish images while runtime identities remain read-only."
  }

  assert {
    condition = length(setintersection(
      toset(concat(
        output.iam_contract.runtime_project_roles.control,
        output.iam_contract.runtime_project_roles.job,
        output.iam_contract.runtime_project_roles.feed,
        output.iam_contract.runtime_project_roles.notebook,
        output.iam_contract.runtime_project_roles.ci,
        output.iam_contract.resource_roles.control,
        output.iam_contract.resource_roles.job,
        output.iam_contract.resource_roles.feed,
        output.iam_contract.resource_roles.notebook,
        output.iam_contract.resource_roles.ci,
        output.iam_contract.runtime_project_roles.recovery,
        output.iam_contract.resource_roles.recovery,
      )),
      toset(["roles/owner", "roles/editor", "roles/run.admin", "roles/storage.admin", "roles/iam.serviceAccountUser"]),
    )) == 0
    error_message = "Runtime and CI identities must not receive broad administrative or impersonation roles."
  }
}

run "github_oidc_trust_boundary" {
  command = plan

  assert {
    condition = (
      length(google_iam_workload_identity_pool_provider.github.attribute_mapping) == 6 &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["google.subject"] == "assertion.sub" &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.repository"] == "assertion.repository" &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.repository_id"] == "assertion.repository_id" &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.repository_owner_id"] == "assertion.repository_owner_id" &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.environment"] == "assertion.environment" &&
      google_iam_workload_identity_pool_provider.github.attribute_mapping["attribute.ref"] == "assertion.ref"
    )
    error_message = "Every claim used by the GitHub trust condition must be explicitly mapped."
  }

  assert {
    condition = alltrue([
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.repository == 'example/jarvis'"),
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.repository_id == '123456789'"),
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.repository_owner_id == '987654321'"),
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.environment == 'development'"),
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "assertion.ref == 'refs/heads/main'"),
    ])
    error_message = "GitHub federation must require the exact repository name and IDs, protected environment, and ref."
  }

  assert {
    condition = (
      google_service_account_iam_member.ci_workload_identity.role == "roles/iam.workloadIdentityUser" &&
      endswith(google_service_account_iam_member.ci_workload_identity.member, "/attribute.repository_id/123456789") &&
      google_service_account_iam_member.ci_workload_identity.service_account_id == google_service_account.roles["ci"].name
    )
    error_message = "Only the configured repository ID may federate into the CI service account."
  }
}

run "invalid_repository_id_is_rejected" {
  command = plan

  variables {
    github_repository_id = "example/jarvis"
  }

  expect_failures = [var.github_repository_id]
}

run "unnamed_deployer_principal_is_rejected" {
  command = plan

  variables {
    deployer_principals = ["allAuthenticatedUsers"]
  }

  expect_failures = [var.deployer_principals]
}

run "automation_operator_is_rejected" {
  command = plan

  variables {
    operator_principals = ["serviceAccount:automation@example.iam.gserviceaccount.com"]
  }

  expect_failures = [var.operator_principals]
}

run "duplicate_storage_boundaries_are_rejected" {
  command = plan

  variables {
    backup_bucket_name = "jarvis-research-dev-data"
  }

  expect_failures = [var.backup_bucket_name]
}

run "production_database_requires_regional_ha" {
  command = plan

  variables {
    env                  = "prod"
    db_availability_type = "ZONAL"
  }

  expect_failures = [google_sql_database_instance.airflow]
}

run "backup_retention_must_cover_pitr_window" {
  command = plan

  variables {
    db_backup_retained_count = 7
  }

  expect_failures = [google_sql_database_instance.airflow]
}

run "staging_integration_identity_is_narrow" {
  command = apply

  variables {
    env                = "stage"
    github_environment = "staging"
  }

  assert {
    condition = (
      length(google_service_account_iam_member.integration_workload_identity) == 1 &&
      google_service_account_iam_member.integration_workload_identity[0].role == "roles/iam.workloadIdentityUser" &&
      google_project_iam_member.integration_cloud_run[0].role == google_project_iam_custom_role.staging_integration[0].name &&
      toset(google_project_iam_custom_role.staging_integration[0].permissions) == toset(["resourcemanager.projects.get", "run.executions.get", "run.executions.list", "run.jobs.create", "run.jobs.delete", "run.jobs.get", "run.jobs.run", "run.jobs.runWithOverrides", "run.locations.get", "run.operations.get"]) &&
      google_service_account_iam_member.integration_act_as_job[0].service_account_id == google_service_account.roles["job"].name &&
      google_service_account_iam_member.integration_act_as_job[0].role == "roles/iam.serviceAccountUser"
    )
    error_message = "Staging integration automation must use its own OIDC identity and may act only as the batch job."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.integration_data[0].role == "roles/storage.objectViewer" &&
      strcontains(google_storage_bucket_iam_member.integration_data[0].condition[0].expression, "/objects/integration/staging_probe/") &&
      google_storage_bucket_iam_member.integration_logs[0].role == google_project_iam_custom_role.staging_integration_logs[0].name &&
      toset(google_project_iam_custom_role.staging_integration_logs[0].permissions) == toset(["storage.objects.create", "storage.objects.delete", "storage.objects.get", "storage.objects.list"]) &&
      strcontains(google_storage_bucket_iam_member.integration_logs[0].condition[0].expression, "/objects/dag_id=staging_integration/") &&
      length(output.iam_contract.runtime_project_roles.integration) == 1 &&
      one(output.iam_contract.runtime_project_roles.integration) == google_project_iam_custom_role.staging_integration[0].name
    )
    error_message = "Integration automation may read only probe output and manage only its Airflow logs."
  }
}

run "production_recovery_automation_is_disabled" {
  command = plan

  variables {
    env                          = "prod"
    db_availability_type         = "REGIONAL"
    db_deletion_protection       = true
    workload_deletion_protection = true
  }

  assert {
    condition = (
      length(google_service_account_iam_member.recovery_workload_identity) == 0 &&
      length(google_project_iam_member.recovery) == 0 &&
      length(google_service_account_iam_member.recovery_act_as_control) == 0 &&
      length(google_storage_bucket_iam_member.recovery_data_canary) == 0 &&
      length(google_storage_bucket_iam_member.recovery_evidence) == 0 &&
      length(google_service_account_iam_member.integration_workload_identity) == 0 &&
      length(google_project_iam_custom_role.staging_integration) == 0 &&
      length(google_project_iam_member.integration_cloud_run) == 0 &&
      length(google_service_account_iam_member.integration_act_as_job) == 0 &&
      length(google_storage_bucket_iam_member.integration_data) == 0 &&
      length(google_project_iam_custom_role.staging_integration_logs) == 0 &&
      length(google_storage_bucket_iam_member.integration_logs) == 0 &&
      !output.recovery_contract.enabled &&
      !output.recovery_contract.production_access
    )
    error_message = "Automated recovery drills must have no production trust or permissions."
  }
}

run "mutable_host_image_reference_is_rejected" {
  command = plan

  variables {
    host_images = {
      control  = "projects/jarvis-research-dev/global/images/family/jarvis-host"
      feed     = "projects/jarvis-research-dev/global/images/jarvis-host-0123456789ab-202609170000"
      notebook = "projects/jarvis-research-dev/global/images/jarvis-host-0123456789ab-202609170000"
    }
  }

  expect_failures = [var.host_images]
}

run "unknown_host_replacement_role_is_rejected" {
  command = plan

  variables {
    host_replacement_role = "all"
  }

  expect_failures = [var.host_replacement_role]
}

run "host_replacement_unlocks_only_one_role" {
  command = plan

  variables {
    workload_deletion_protection = true
    host_replacement_role        = "feed"
  }

  assert {
    condition = (
      google_compute_instance.control.deletion_protection &&
      !google_compute_instance.feed.deletion_protection &&
      google_compute_instance.notebook.deletion_protection &&
      google_cloud_run_v2_job.research.deletion_protection
    )
    error_message = "A host replacement may disable deletion protection only for its selected VM role."
  }
}
