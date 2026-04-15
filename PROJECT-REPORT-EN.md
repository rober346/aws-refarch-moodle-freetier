# PROJECT REPORT: Adapting aws-refarch-moodle for AWS Free Tier

**Base repository:** [aws-samples/aws-refarch-moodle](https://github.com/aws-samples/aws-refarch-moodle)  
**Final status:** Moodle 4.5 LTS deployed and running on AWS

---

## 1. Executive Summary

This project took an official Amazon Web Services (AWS) repository that was designed to deploy the Moodle education platform in the cloud, and made it work on an AWS "Free Tier" account (the free plan that AWS offers to new accounts). The original repository was built for large company production environments with expensive resources, and when you tried to deploy it as-is on a free account, it failed immediately without showing clear error messages.

During several work sessions, we found ten different errors, fixed the configuration files, and made the system deploy in a fully automatic way. The final result is that when you launch the stack with the correct parameters, Moodle is installed and ready to use with no manual steps.

The modified repository can be reused by anyone who wants to deploy Moodle on AWS without unnecessary costs, just by following the guide included at the end of this document.

---

## 2. The Original Problem

The `aws-samples/aws-refarch-moodle` repository is a reference architecture published by AWS that deploys Moodle on multiple servers with high availability. It uses CloudFormation (the AWS tool for creating infrastructure using YAML configuration files) and manages the creation of dozens of resources at the same time.

The problem is that the repository was designed for production environments and uses advanced services that **are not available on Free Tier accounts**:

- **Aurora Serverless**: AWS serverless database, blocked on free accounts with the message "FreeTierRestrictionError" — an error that CloudFormation did not show in its interface, making it very hard to diagnose.
- **ElastiCache Serverless**: serverless cache system, which only accepts the "Redis" or "Valkey" engine, but the template used "Memcached" by default.
- **CodeDeploy / CodePipeline**: continuous code delivery services, also blocked on Free Tier.
- **Expensive instances**: the default configuration used `db.r6g.large` database instances and `c7g.xlarge` EC2 servers, both not allowed on free accounts.

Also, the original template had configuration errors that were not related to Free Tier: invalid database engine versions, incorrect default values, and a Moodle installation process that depended completely on CodePipeline (which was disabled). When CloudFormation detected any of these problems, it started a rollback (the process of undoing everything that was created), which sometimes also failed, leaving active resources that continued to cost money.

---

## 3. Errors Found and How They Were Fixed

### Error 0 — NumberOfAZs = 1 (Initial deploy, before the project)

**What the error said:** In CloudTrail (the AWS audit log), we saw `DBSubnetGroupDoesNotCoverEnoughAZs` and `InvalidParameterValueException` in ElastiCache.

**Why it happened:** The user launched the stack with `NumberOfAZs=1` (only one Availability Zone — *a virtual datacenter in AWS*). Aurora Serverless requires at least 2 zones, and ElastiCache in `cross-az` mode does too.

**How it was fixed:** `NumberOfAZs=2` was set as the minimum requirement when launching the stack. This did not require any code changes.

---

### Error 1 — ElastiCache Serverless does not support Memcached

**What the error said:** The `sessioncache` and `applicationcache` sub-stacks failed with `CREATE_FAILED`.

**Why it happened:** The original template used `AWS::ElastiCache::ServerlessCache` (the serverless version of ElastiCache), but that resource only accepts `redis` or `valkey` as engines. The `CacheEngineType` parameter had `Memcached` as its default value, which is not compatible.

**How it was fixed:** The parameters `UseServerlessSessionCache=false` and `UseServerlessApplicationCache=false` were passed when creating the stack, forcing the use of standard ElastiCache (with instances). No template changes were needed; it was enough to select the correct value when launching the stack.

**Modified file:** None (parameter change in the console).

---

### Error 2 — Invalid Aurora Serverless EngineVersion

**What the error said:** `DatabaseCluster CREATE_FAILED` with no clear message in CloudFormation. In CloudTrail: the `CreateDBCluster` call never appeared, which meant the template was badly formed.

**Why it happened:** The template `03-rdsserverless.yaml` did not specify `EngineVersion` for the Aurora cluster. AWS rejected the request silently.

**How it was fixed:** `EngineVersion: !If [ UsePostgreSQL, '16.4', '8.0.mysql_aurora.3.08.0' ]` was added to the `DatabaseCluster` resource.

**Modified file:** `templates/03-rdsserverless.yaml`

> **Note:** This fix was included in the code but in practice became irrelevant, because Error 3 forced us to abandon Aurora completely.

---

### Error 3 — Aurora blocked on Free Tier accounts (main root cause)

**What the error said:** `DatabaseCluster` kept failing even though `EngineVersion` was already valid. The real message, obtained directly via AWS CLI (*AWS command-line interface*), was: `FreeTierRestrictionError: To use Aurora clusters with free plan accounts you need to set WithExpressConfiguration`. CloudFormation **did not show this error in its visual interface**, making it invisible and hard to diagnose.

**Why it happened:** AWS blocks the creation of Aurora clusters on Free Tier accounts unless a special configuration (`WithExpressConfiguration`) is used, which CloudFormation does not support directly.

**How it was fixed:** `templates/03-rds.yaml` was completely rewritten to use `AWS::RDS::DBInstance` (standard RDS) instead of `AWS::RDS::DBCluster` (Aurora). Standard RDS with MySQL is available on Free Tier. The modified template keeps the same SSM parameters (*System Manager Parameter Store — AWS configuration storage*) and Outputs that the rest of the stack expects, so the change was transparent.

**Modified file:** `templates/03-rds.yaml` — completely rewritten.

---

### Error 4 — RDS instance `db.t3.medium` blocked on Free Tier

**What the error said:** `DatabaseInstance CREATE_FAILED` with the message: "This instance size isn't available with free plan accounts."

**Why it happened:** The `DatabaseInstanceType` parameter had `db.r6g.large` as its original default. It had been changed to `db.t3.medium`, but this instance is also not available on Free Tier. The only RDS instance eligible for Free Tier is `db.t3.micro`.

**How it was fixed:** `db.t3.micro` was added to `AllowedValues` and the `Default` was changed to `db.t3.micro` in both files where the parameter appears.

**Modified files:** `templates/00-main.yaml`, `templates/03-rds.yaml`

---

### Error 5 — EC2 instance `t3.medium` blocked on Free Tier

**What the error said:** `WebAutoScalingGroup` (*web server auto-scaling group*) never launched any instances. The ASG retried silently every ~8 minutes, without reporting any error to CloudFormation. The real cause was found by checking the ASG directly via CLI: "The specified instance type is not eligible for Free Tier."

**Why it happened:** The Auto Scaling Group (ASG — *the AWS service that launches and manages servers automatically*) tried to create `t3.medium` instances, which are not eligible for Free Tier. When it failed, it retried without telling CloudFormation, causing the stack to get "stuck" for almost 1 hour.

**How it was fixed:** `t3.micro` was used as the EC2 instance type, the only one eligible on Free Tier. It was already in the template's `AllowedValues`, so it was enough to pass it as a parameter. The default was changed in `00-main.yaml`.

**Modified file:** `templates/00-main.yaml` (default changed in `WebInstanceType`)

---

### Error 6 — Rollback blocked: S3 bucket with ALB logs

**What the error said:** The `publicalb` sub-stack remained in `DELETE_FAILED` during rollback with the message: "The bucket you tried to delete is not empty."

**Why it happened:** The ALB (*Application Load Balancer — the load balancer that distributes web traffic*) automatically writes access logs to an S3 bucket. When CloudFormation tries to rollback, it tries to delete that bucket, but it fails because S3 does not allow deleting buckets that have content.

**How it was fixed:** The bucket was emptied manually with `aws s3 rm s3://<bucket-name> --recursive` before starting the stack delete. This procedure was documented as a standard step for all deploys.

**Modified file:** None. This is an operational procedure required before each delete.

```bash
# Command to empty the ALB bucket before deleting the stack:
aws s3 ls | grep <stack-name>
aws s3 rm s3://<alb-bucket-name> --recursive --region us-east-1
```

---

### Error 7 — PHP `zip.so` causes segfault on `t3.micro` instances

**What the error said:** `WebAutoScalingGroup CREATE_FAILED`. In the EC2 instance log (obtained with `aws ec2 get-console-output`): `php segfault at ... in zip.so (deleted)`. Followed by: `Error occurred during build: Command create_site_conf failed`.

**Why it happened:** The instance configuration script used `pecl install zip` to compile the PHP extension for handling ZIP files. On `t3.micro` (1GB of RAM), compiling from source code required more memory than was available and the operating system process failed with a fatal error ("segmentation fault" — *unauthorized memory access*).

**How it was fixed:** `php8.1-zip` was installed directly from the operating system package repository (Amazon Linux 2023), removing the need to compile. `php8.1-zip: []` was added to the `cfn-init` installation block (*the AWS tool for configuring EC2 instances*) and the `pecl install zip` lines and the manual creation of the `50-zip.ini` file were removed.

**Modified file:** `templates/04-web.yaml`

---

### Error 8 — CodeDeploy blocked on Free Tier accounts

**What the error said:** The `codePipeline` sub-stack → `MoodleDeployApp` resource (`AWS::CodeDeploy::Application`) was left in `CREATE_FAILED` and then in `DELETE_FAILED`. The real message via CLI: "The AWS Access Key Id needs a subscription for the service (Service: CodeDeploy, Status Code: 400)."

**Why it happened:** AWS CodeDeploy (*the service for automatic code delivery to servers*) is not available on Free Tier accounts. The original template assumed it would always be available.

**How it was fixed:** A `DeployPipeline` parameter (boolean type, default `false`) was added to `00-main.yaml`. The conditions `DeployUsingRDSInstances` and `DeployUsingRDSServerless` were refactored into more specific conditions `DeployCodePipeline` and `DeployCodePipelineServerless` that require both the correct `DatabaseUseServerless` value and `DeployPipeline=true`. With `DeployPipeline=false` (default), the CodePipeline/CodeDeploy sub-stack is not created.

**Modified files:** `templates/00-main.yaml`

> **Implication:** By disabling CodePipeline, Moodle stopped installing automatically (CodePipeline was the mechanism that copied the Moodle files to the server). This created Error 9.

---

### Error 9 — Moodle was not installing: only "It works!" appeared

**What the error said:** Stack in `CREATE_COMPLETE`, the ALB responded, but the browser showed "It works!" (the default Apache page — *the web server*), not Moodle.

**Why it happened:** With CodePipeline disabled, the Moodle files never reached the `/var/www/moodle/html/` directory on the EC2 server. The original architecture depended on CodePipeline for this task.

**How it was fixed:** A direct download block was added in the `create_site_conf.sh` script inside `04-web.yaml`. The script now downloads Moodle directly from `download.moodle.org` during instance startup, if the files are not already present. Moodle 4.5 LTS was selected (support until October 2027, compatible with PHP 8.1 already installed on the system).

```bash
# Logic added to the configuration script:
if [ ! -f /var/www/moodle/html/index.php ]; then
  wget -O /tmp/moodle.tgz ${MoodleDirectDownloadURL}
  tar -xvzf /tmp/moodle.tgz --strip-components=1 -C /var/www/moodle/html/
  chown -R apache:apache /var/www/moodle/html/
  rm -f /tmp/moodle.tgz
fi
```

**Modified files:** `templates/04-web.yaml`, `templates/00-main.yaml`

---

### Error 10 — ASG was cycling instances indefinitely (502 Bad Gateway)

**What the error said:** Stack `CREATE_COMPLETE`, but the browser returned 502 (*bad gateway error*). The instances in the target group (*the load balancer's destination*) appeared as `unhealthy`, were terminated and replaced continuously.

**Why it happened:** The `HealthCheckGracePeriod` (*the waiting period before checking if an instance is healthy*) was set to 120 seconds. The ALB performs 5 health checks with 30-second intervals, needing at least 150 seconds to approve an instance. With only 120s of grace time, the ASG checked health before the ALB finished its process, marked the instance as unhealthy, and terminated it to launch another one.

**How it was fixed:** `HealthCheckGracePeriod` was changed from `120` to `300` seconds in `04-web.yaml`. This gives enough time for Apache to start, cfn-init (*instance configuration tool*) to finish, and the ALB to complete its health checks.

**Modified file:** `templates/04-web.yaml`

---

### Error 11 — Moodle root directory without write permissions

**What the error said:** The web installation wizard showed: "The parent directory (/var/www/moodle) is not writable."

**Why it happened:** The `/var/www/moodle` directory was owned by the `root` user, but Apache runs as the `apache` user. Apache could not write there.

**How it was fixed:** `chown apache:apache /var/www/moodle` and `chmod 755 /var/www/moodle` were run via SSM (*Systems Manager — the AWS tool for running commands on servers without SSH access*). This fix was later included as part of the automation in `04-web.yaml`.

**Modified file:** `templates/04-web.yaml` (fix included in the later automation)

---

### Error 12 — RDS created with PostgreSQL instead of MySQL

**What the error said:** The web installation wizard could not connect to the database. When checking via CLI: the RDS had `Engine: postgres` and port `5432`, but Moodle tried to connect on port `3306` (MySQL).

**Why it happened:** The `DatabaseType` parameter had `Default: PostgreSQL` in `00-main.yaml`. When launching the stack without specifying that parameter, the RDS was created with PostgreSQL instead of MySQL. The database security group (*firewall rule in AWS*) only had port 5432 open, blocking any MySQL connection to port 3306.

**How it was fixed:** `Default: PostgreSQL` was changed to `Default: MySQL` in the `DatabaseType` parameter in `00-main.yaml`.

**Modified file:** `templates/00-main.yaml`

> **Discrepancy found in the current code:** `03-rds.yaml` still shows `Default: PostgreSQL` in its own `DatabaseType` parameter. However, this does not affect the real behavior: when deployed through `00-main.yaml`, the `MySQL` value is passed explicitly to the sub-stack, overriding that default. The default in `03-rds.yaml` only applies if someone deploys that template on its own, outside of the main stack.

---

### Error 13 — MySQL 8.0.39 retired by AWS

**What the error said:** `DatabaseInstance CREATE_FAILED`. AWS no longer offered version `8.0.39` of MySQL.

**Why it happened:** AWS periodically retires database engine versions. Version `8.0.39` had been removed from the RDS catalog.

**How it was fixed:** The `EngineVersion` was updated to `'8.0.45'` in `03-rds.yaml`, which was the available version at the time of the fix.

**Modified file:** `templates/03-rds.yaml`

> **Note for the future:** If the deploy fails because of an invalid `EngineVersion`, check the available versions with: `aws rds describe-db-engine-versions --engine mysql --query "DBEngineVersions[*].EngineVersion" --output table --region us-east-1`

---

### Error 14 — Badly formed YAML caused by PHP scripts inside `!Sub` blocks

**What the error said:** `webapp CREATE_FAILED — Template format error: YAML not well-formed (line 676, column 1)`.

**Why it happened:** The configuration script included PHP code inside a `content: !Sub |` cfn-init block. CloudFormation processes `!Sub` to replace variables, but the YAML parser interprets lines that start at column 0 as the end of the literal block. The `$` character in the PHP code also conflicted with the `!Sub` variable processing.

**How it was fixed:** The PHP scripts were moved to separate cfn-init files using `content: |` (without `!Sub`). These files are not processed by the CloudFormation variable substitution system, so the `$` characters in PHP do not cause any conflict.

**Modified file:** `templates/04-web.yaml`

---

### Error 15 — "Database tables already present" on the second ASG instance

**What the error said:** cfn-init log on the second instance: "Database tables already present; CLI installation cannot continue."

**Why it happened:** The ASG launched a second replacement instance because the first one took longer than `HealthCheckGracePeriod` to complete cfn-init (PECL extension compilation took ~15 minutes). The second instance did not have `config.php` locally, but the database already had the ~400 tables installed by the first instance. The Moodle CLI installer refuses to install on a database that already has data.

**How it was fixed:** Detection logic was added to the script: if the database already has more than 10 `mdl_` tables, `config.php` is regenerated locally (without reinstalling) using a helper PHP script (`gen_moodle_config.php`). This allows multiple instances to start correctly when the database is already initialized.

**Modified file:** `templates/04-web.yaml`

---

## 4. Changes Made to the Code

### `templates/00-main.yaml`

| Aspect | Before | After |
|---|---|---|
| `DatabaseType` default | `PostgreSQL` | `MySQL` |
| `DatabaseUseServerless` default | `true` | `false` |
| `DatabaseInstanceType` default | `db.r6g.large` | `db.t3.micro` |
| `UseServerlessSessionCache` default | `true` | `false` |
| `UseServerlessApplicationCache` default | `true` | `false` |
| `SessionCacheNodeType` default | `cache.r6g.large` | `cache.t3.micro` |
| `ApplicationCacheNodeType` default | `cache.r6g.large` | `cache.t3.micro` |
| `WebInstanceType` default | `c7g.xlarge` | `t3.micro` |
| `MoodleDirectDownloadURL` default | Moodle 4.4 (no longer supported) | Moodle 4.5 LTS |
| `DeploymentLocation` default | Official AWS URL | Placeholder `https://<YOUR_BUCKET_NAME>.s3.<YOUR_REGION>.amazonaws.com/templates` |
| `MoodleAdminPassword` default | `MoodleAdmin1!` | `<YOUR_ADMIN_PASSWORD>` |
| `MoodleAdminEmail` default | (real address) | `<YOUR_ADMIN_EMAIL>` |
| `DeployPipeline` parameter | Did not exist | Added, default `false` |
| `MoodleAdminUser`, `MoodleAdminPassword`, `MoodleAdminEmail` parameters | Did not exist | Added and passed to the `webapp` sub-stack |
| `PublicAlbDnsName` parameter in `webapp` | Did not exist | Added (needed to build the Moodle `wwwroot`) |
| CodePipeline conditions | `DeployUsingRDSInstances` / `DeployUsingRDSServerless` | `DeployCodePipeline` / `DeployCodePipelineServerless` (require `DeployPipeline=true`) |
| `ExcludeCharacters` in RDS secret | `'"@/\'` | `'"@/\$\`` ` (excludes `$` and backticks that broke the bash script) |

**Why:** This file is the entry point for the entire deploy. Setting the correct defaults here means the user does not have to remember dozens of parameters every time they launch the stack.

---

### `templates/03-rds.yaml`

| Aspect | Before | After |
|---|---|---|
| Database resource type | `AWS::RDS::DBCluster` + `AWS::RDS::DBInstance` (Aurora) | A single `AWS::RDS::DBInstance` (standard RDS) |
| `DatabaseInstanceType` in `AllowedValues` | Did not include `db.t3.micro` | Includes `db.t3.micro` as first option and default |
| MySQL `EngineVersion` | `8.0.39` (retired) | `8.0.45` |
| PostgreSQL `EngineVersion` | Not specified | `16.3` |
| Generated SSM Parameters | Aurora cluster endpoint | RDS instance endpoint (same value for read/write) |
| Outputs | Oriented to Aurora | Adapted for single instance; `DatabaseInstance0` and `DatabaseInstance1` point to the same resource |

**Why:** Aurora is blocked on Free Tier accounts. Standard RDS with MySQL has the same functional behavior for Moodle and is compatible with the free plan. The SSM Parameters and Outputs were kept with the same names to avoid breaking the rest of the stack.

---

### `templates/03-rdsserverless.yaml`

| Aspect | Before | After |
|---|---|---|
| `EngineVersion` in `DatabaseCluster` | Not specified | `!If [ UsePostgreSQL, '16.4', '8.0.mysql_aurora.3.08.0' ]` |

**Why:** Without `EngineVersion`, AWS silently rejected the cluster creation. This fix is correct but in practice it is not used when `DatabaseUseServerless=false` (which is the recommended default for Free Tier).

---

### `templates/03-elasticache.yaml`

| Aspect | Before | After |
|---|---|---|
| `AZMode` in Memcached cluster | `cross-az` (hardcoded) | `!If [ NumberOfSubnets1, single-az, cross-az ]` |
| `ElastiCacheNodeType` default | `cache.r6g.large` | `cache.t3.micro` (in `00-main.yaml` which controls it) |

**Why:** With `NumberOfAZs=1`, the `cross-az` mode (which distributes nodes across multiple zones) was invalid. The conditional selects `single-az` when there is only one subnet, avoiding the error.

---

### `templates/04-web.yaml`

| Aspect | Before | After |
|---|---|---|
| ASG `HealthCheckGracePeriod` | `120` seconds | `300` seconds |
| `CreationPolicy.Timeout` | `PT15M` (15 minutes) | `PT60M` (60 minutes) |
| PHP ZIP extension installation | `pecl install zip` (compilation from source) | `php8.1-zip: []` (system package, no compilation) |
| Moodle download | Did not exist (depended on CodePipeline) | Direct download from `download.moodle.org` if files are not present |
| Automatic Moodle installation | Did not exist | `admin/cli/install.php` script run by cfn-init |
| Second ASG instance handling | Did not exist (failed) | Database table detection → regenerate `config.php` without reinstalling |
| PHP scripts in cfn-init | Inside `!Sub` blocks (broke YAML) | Separate files `fix_moodle_post.php` and `gen_moodle_config.php` with `content: \|` |
| `--wwwroot` when installing Moodle | Not applied | Built in lowercase with `tr '[:upper:]' '[:lower:]'` |
| `cookiesecure` post-installation | Not corrected | Automatically set to `0` (the site uses HTTP, not HTTPS) |
| New parameters | N/A | `PublicAlbDnsName`, `DatabaseName`, `DatabaseType`, `MoodleAdminUser`, `MoodleAdminPassword`, `MoodleAdminEmail`, `MoodleLocale`, `MoodleDirectDownloadURL` |

**Why:** This file controls everything that happens when an EC2 server starts. The changes were needed so that:
1. The instance could start correctly on `t3.micro` (no zip.so segfault)
2. Moodle would install on its own, without manual steps or CodePipeline
3. The ASG could manage multiple instances without installation conflicts
4. CloudFormation would wait long enough for everything to finish

---

## 5. AWS Free Tier Limitations Discovered

The following restrictions **are not documented in the original repository** and cause silent failures or unclear error messages:

| # | Service / Resource | Restriction | Error observed |
|---|---|---|---|
| 1 | **Aurora RDS** (any mode) | Completely blocked. Returns `FreeTierRestrictionError` which CloudFormation **does not show** in its console — only visible via AWS CLI with `describe-stack-events`. | `DatabaseCluster CREATE_FAILED` with no apparent reason |
| 2 | **RDS instances** | Only `db.t3.micro` is allowed. `db.t3.medium` and larger return an explicit error. | "This instance size isn't available with free plan accounts." |
| 3 | **EC2 instances** | Only `t3.micro` (and Free Tier equivalent types) are eligible. The Auto Scaling Group fails silently, retrying every ~8 minutes without reporting the error to CloudFormation. | Stack "stuck" for 45-60 min with no error message |
| 4 | **ElastiCache Serverless** | `AWS::ElastiCache::ServerlessCache` only accepts `redis` or `valkey`, not `Memcached`. The original template uses Memcached by default. | `sessioncache CREATE_FAILED` |
| 5 | **AWS CodeDeploy** | Not available on Free Tier accounts. Returns HTTP 400 with "needs a subscription". The resource also gets stuck in `DELETE_FAILED` during rollback (requires `--retain-resources`). | `MoodleDeployApp CREATE_FAILED` and then `DELETE_FAILED` |
| 6 | **PHP extension compilation** on `t3.micro` | The `t3.micro` instance has 1GB of RAM. Compiling PHP extensions with `pecl install` can cause a `segfault` due to insufficient memory. | `zip.so segfault` → `create_site_conf failed` |
| 7 | **RDS engine versions** | AWS retires engine versions periodically. `MySQL 8.0.39` was retired. Templates with hardcoded versions stop working without any warning. | `EngineVersion invalid` |
| 8 | **ALB S3 bucket with logs** | CloudFormation cannot delete an S3 bucket that has content. The ALB writes logs constantly, so the bucket always has data at the time of rollback. This blocks any stack delete. | `publicalb DELETE_FAILED` |

---

## 6. Final Result

### Deploy Status

After 14 deploy attempts (NewMoodle1 to NewMoodle14) and accumulated fixes in each session, the stack reached `CREATE_COMPLETE` with Moodle 4.5 LTS completely installed and working.

The NewMoodle14 stack was the first one to achieve a **fully automated installation**: when you launch the stack with the correct parameters, Moodle is installed and accessible with no manual steps.

### What Was Created in AWS

| Resource | Type | Description |
|---|---|---|
| VPC | Virtual private network | Isolates all project resources |
| Public subnets (x2) | Networks inside the VPC | For the load balancer |
| Application subnets (x2) | Networks inside the VPC | For the EC2 servers |
| Data subnets (x2) | Networks inside the VPC | For RDS and ElastiCache |
| RDS MySQL 8.0.45 | Database | Moodle persistence engine |
| ElastiCache Memcached | Cache | Stores sessions and content in memory |
| EFS | File system | Shared storage for Moodle files |
| ALB | Load balancer | Distributes HTTP traffic to the servers |
| Auto Scaling Group | EC2 management | Launches and maintains the EC2 instance |
| EC2 `t3.micro` | Web server | Runs Apache + PHP + Moodle |

### How to Access Moodle

The access URL follows the pattern of the ALB DNS name. You can get it with:

```bash
aws ssm get-parameter \
  --name "/Moodle/<STACK_NAME>/Network/DomainName" \
  --region us-east-1 \
  --query "Parameter.Value" \
  --output text
```

### Example Credentials (from the NewMoodle14 deploy)

> **Important:** These credentials are from a test stack that was deleted. They are not active. They are here as a format reference.

| Field | Example value |
|---|---|
| URL | `http://<alb-dns>.us-east-1.elb.amazonaws.com` |
| Admin user | `admin` (or the value of `MoodleAdminUser`) |
| Admin password | The value passed in `MoodleAdminPassword` when creating the stack |

---

## 7. Guide to Reproduce It

This guide lets anyone deploy Moodle on an AWS Free Tier account from scratch.

### Requirements

- Active AWS account (Free Tier or higher)
- AWS CLI installed and configured with valid credentials
- Git installed

### Step 1 — Clone the repository

```bash
git clone https://github.com/<your-username>/aws-refarch-moodle.git
cd aws-refarch-moodle
```

### Step 2 — Create an S3 bucket for the templates

CloudFormation needs the templates to be accessible in S3. Create your own bucket:

```bash
# Choose a unique name for your bucket (it cannot be repeated globally in AWS)
BUCKET_NAME="moodle-templates-$(date +%s)"
REGION="us-east-1"

aws s3 mb s3://$BUCKET_NAME --region $REGION

# Upload all templates
aws s3 cp templates/ s3://$BUCKET_NAME/templates/ --recursive --region $REGION

echo "Your templates URL: https://$BUCKET_NAME.s3.$REGION.amazonaws.com/templates"
```

### Step 3 — Launch the stack in CloudFormation

Open the AWS console → CloudFormation → "Create stack" → "With new resources".

**Template URL:**
```
https://<YOUR_BUCKET>.s3.us-east-1.amazonaws.com/templates/00-main.yaml
```

**Parameters you MUST fill in (the rest have correct defaults):**

| Parameter | Value to enter |
|---|---|
| `DeploymentLocation` | `https://<YOUR_BUCKET>.s3.us-east-1.amazonaws.com/templates` |
| `AvailabilityZones` | Select: `us-east-1a` and `us-east-1b` |
| `NotifyEmailAddress` | Your real email |
| `MoodleAdminPassword` | A strong password (minimum 8 characters, no spaces, `$`, quotes, or backslashes) |
| `MoodleAdminEmail` | Your email for the Moodle admin account |

**Parameters with defaults already correct for Free Tier (do not change):**

| Parameter | Configured default | Why |
|---|---|---|
| `DatabaseType` | `MySQL` | MySQL works with Free Tier; PostgreSQL has less support in Moodle |
| `DatabaseUseServerless` | `false` | Aurora Serverless is blocked on Free Tier |
| `DatabaseInstanceType` | `db.t3.micro` | Only RDS instance eligible on Free Tier |
| `UseServerlessSessionCache` | `false` | ElastiCache Serverless does not support Memcached |
| `UseServerlessApplicationCache` | `false` | Same as above |
| `SessionCacheNodeType` | `cache.t3.micro` | Most economical option available |
| `ApplicationCacheNodeType` | `cache.t3.micro` | Same as above |
| `WebInstanceType` | `t3.micro` | Only EC2 type eligible on Free Tier |
| `DeployPipeline` | `false` | CodeDeploy is blocked on Free Tier |
| `NumberOfAZs` | `2` | Minimum required by RDS and ElastiCache |

### Step 4 — Monitor the progress

The stack can take between 30 and 50 minutes. You can monitor it in the CloudFormation console or via CLI:

```bash
# Check the main stack status
aws cloudformation describe-stacks \
  --stack-name <STACK_NAME> \
  --region us-east-1 \
  --query "Stacks[0].StackStatus" \
  --output text
```

Expected order of sub-stack creation:

1. `vpc` → `CREATE_COMPLETE`
2. `securitygroups` → `CREATE_COMPLETE`
3. `rds`, `sessioncache`, `sharedEFS`, `publicalb`, `pipelineHelper` → in parallel
4. `webapp` → `CREATE_COMPLETE` (takes longer, includes Moodle download and installation)

### Step 5 — Get the Moodle URL

Once the stack is in `CREATE_COMPLETE`:

```bash
aws ssm get-parameter \
  --name "/Moodle/<STACK_NAME>/Network/DomainName" \
  --region us-east-1 \
  --query "Parameter.Value" \
  --output text
```

Open that URL in the browser. Moodle should be installed and ready to log in.

### Step 6 — First login

- **User:** the value of `MoodleAdminUser` (default: `admin`)
- **Password:** the value of `MoodleAdminPassword` that you entered in Step 3

It is recommended to change the password on the first login from **Site administration → Users → Accounts → User profiles**.

### Step 7 — Delete the stack (when no longer needed)

To avoid costs, delete the stack when it is not in use. **Before deleting it**, empty the ALB bucket:

```bash
# 1. Find the ALB bucket
aws s3 ls | grep <stack-name-in-lowercase>
# Look for the bucket that says "loadbalanceraccesslogs"

# 2. Empty it
aws s3 rm s3://<alb-bucket-name> --recursive --region us-east-1

# 3. Also empty the pipelineHelper buckets if they exist
aws s3 rm s3://<codeartifacts-bucket-name> --recursive --region us-east-1
aws s3 rm s3://<moodlegit-bucket-name> --recursive --region us-east-1

# 4. Delete the stack
aws cloudformation delete-stack --stack-name <STACK_NAME> --region us-east-1
```

> **Why you need to empty the bucket first:** CloudFormation cannot delete S3 buckets that have content. The ALB writes access logs constantly, so the bucket always has files. If you do not empty it first, the `publicalb` sub-stack gets stuck in `DELETE_FAILED` and you have to clean it up manually.

### How to Clean Up a Stack in DELETE_FAILED

If the stack gets stuck in `ROLLBACK_FAILED` or `DELETE_FAILED`:

```bash
# If the publicalb sub-stack got blocked:
# 1. Empty the bucket (see Step 7)
# 2. Retry the parent stack delete:
aws cloudformation delete-stack --stack-name <STACK_NAME> --region us-east-1

# If the codePipeline sub-stack got stuck in DELETE_FAILED because of MoodleDeployApp:
aws cloudformation delete-stack \
  --stack-name <STACK>-codePipeline-XXXX \
  --retain-resources MoodleDeployApp \
  --region us-east-1
# Then delete the parent stack normally
```
