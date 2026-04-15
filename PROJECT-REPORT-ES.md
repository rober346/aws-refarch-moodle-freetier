# REPORTE DE PROYECTO: Adaptación de aws-refarch-moodle para AWS Free Tier

**Repositorio base:** [aws-samples/aws-refarch-moodle](https://github.com/aws-samples/aws-refarch-moodle)  
**Estado final:** Moodle 4.5 LTS desplegado y operativo en AWS

---

## 1. Resumen ejecutivo

Este proyecto consistió en tomar un repositorio oficial de Amazon Web Services (AWS) diseñado para desplegar la plataforma educativa Moodle en la nube, y hacerlo funcionar en una cuenta de AWS tipo "Free Tier" (la capa gratuita que AWS ofrece a cuentas nuevas). El repositorio original estaba construido para entornos de producción empresarial con recursos costosos, y al intentar desplegarlo tal cual en una cuenta gratuita, fallaba inmediatamente sin dar mensajes de error claros.

A lo largo de varias sesiones de trabajo, se identificaron diez errores distintos, se corrigieron los archivos de configuración correspondientes, y se logró que el sistema se desplegara de forma completamente automática. El resultado final es que al lanzar el stack con los parámetros correctos, Moodle queda instalado y listo para usar sin ninguna intervención manual.

El repositorio modificado puede ser reutilizado por cualquier persona que quiera desplegar Moodle en AWS sin incurrir en costos innecesarios, simplemente siguiendo la guía incluida al final de este documento.

---

## 2. El problema original

El repositorio `aws-samples/aws-refarch-moodle` es una arquitectura de referencia publicada por AWS que despliega Moodle en múltiples servidores con alta disponibilidad. Utiliza CloudFormation (la herramienta de AWS para crear infraestructura mediante archivos de configuración YAML) y orquesta la creación de decenas de recursos en paralelo.

El problema es que el repositorio fue diseñado para entornos de producción y hace uso intensivo de servicios avanzados que **no están disponibles en cuentas Free Tier**:

- **Aurora Serverless**: base de datos sin servidor de AWS, bloqueada en cuentas gratuitas con el mensaje "FreeTierRestrictionError" — un error que CloudFormation no mostraba en su interfaz, haciendo el diagnóstico muy difícil.
- **ElastiCache Serverless**: sistema de caché sin servidor, que además solo acepta el motor "Redis" o "Valkey", pero el template usaba "Memcached" por defecto.
- **CodeDeploy / CodePipeline**: servicios de entrega continua de código, igualmente bloqueados en Free Tier.
- **Instancias costosas**: la configuración por defecto usaba instancias de base de datos `db.r6g.large` y servidores EC2 `c7g.xlarge`, ambas prohibidas en cuentas gratuitas.

Adicionalmente, el template original tenía errores de configuración independientes del Free Tier: versiones de motor de base de datos inválidas, valores por defecto incorrectos, y una lógica de instalación de Moodle que dependía completamente de CodePipeline (el cual se desactivó). Cuando CloudFormation detectaba alguno de estos problemas, iniciaba un rollback (proceso de deshacer todo lo creado) que a veces también fallaba, dejando recursos activos cobrando dinero.

---

## 3. Errores encontrados y cómo se resolvieron

### Error 0 — NumberOfAZs = 1 (Deploy inicial, antes del proyecto)

**Qué decía el error:** En CloudTrail (el registro de auditoría de AWS) se observó `DBSubnetGroupDoesNotCoverEnoughAZs` y `InvalidParameterValueException` en ElastiCache.

**Por qué ocurría:** El usuario lanzó el stack con `NumberOfAZs=1` (una sola Zona de Disponibilidad — *datacenter virtual de AWS*). Aurora Serverless requiere mínimo 2 zonas, y ElastiCache en modo `cross-az` también.

**Cómo se resolvió:** Se estableció `NumberOfAZs=2` como requisito mínimo al lanzar el stack. Esto no requirió cambios en el código.

---

### Error 1 — ElastiCache Serverless no soporta Memcached

**Qué decía el error:** Los sub-stacks `sessioncache` y `applicationcache` fallaban en `CREATE_FAILED`.

**Por qué ocurría:** El template original usaba `AWS::ElastiCache::ServerlessCache` (la versión sin servidor de ElastiCache), pero ese recurso solo acepta los motores `redis` o `valkey`. El parámetro `CacheEngineType` tenía `Memcached` como valor por defecto, lo cual es incompatible.

**Cómo se resolvió:** Se pasaron los parámetros `UseServerlessSessionCache=false` y `UseServerlessApplicationCache=false` al crear el stack, forzando el uso de ElastiCache estándar (con instancias). Esto no requirió modificar el template; bastó con seleccionar el valor correcto al lanzar el stack.

**Archivo modificado:** Ninguno (cambio de parámetro en la consola).

---

### Error 2 — EngineVersion de Aurora Serverless inválida

**Qué decía el error:** `DatabaseCluster CREATE_FAILED` sin mensaje claro en CloudFormation. En CloudTrail: la llamada `CreateDBCluster` nunca aparecía, lo que indicaba que el template estaba mal formado.

**Por qué ocurría:** El template `03-rdsserverless.yaml` no especificaba `EngineVersion` para el cluster Aurora. AWS rechazaba la petición silenciosamente.

**Cómo se resolvió:** Se agregó `EngineVersion: !If [ UsePostgreSQL, '16.4', '8.0.mysql_aurora.3.08.0' ]` al recurso `DatabaseCluster`.

**Archivo modificado:** `templates/03-rdsserverless.yaml`

> **Nota:** Este fix quedó en el código pero en la práctica se volvió irrelevante, porque el Error 3 obligó a abandonar Aurora completamente.

---

### Error 3 — Aurora bloqueado en cuentas Free Tier (causa raíz principal)

**Qué decía el error:** `DatabaseCluster` seguía fallando aunque `EngineVersion` ya era válida. El mensaje real, obtenido directamente via AWS CLI (*interfaz de línea de comandos de AWS*), era: `FreeTierRestrictionError: To use Aurora clusters with free plan accounts you need to set WithExpressConfiguration`. CloudFormation **no mostraba este error en su interfaz gráfica**, lo que lo hacía invisible y difícil de diagnosticar.

**Por qué ocurría:** AWS bloquea la creación de clusters Aurora en cuentas Free Tier a menos que se use una configuración especial (`WithExpressConfiguration`) que CloudFormation no soporta directamente.

**Cómo se resolvió:** Se reescribió completamente `templates/03-rds.yaml` para usar `AWS::RDS::DBInstance` (RDS estándar) en lugar de `AWS::RDS::DBCluster` (Aurora). RDS estándar con MySQL sí está disponible en Free Tier. El template modificado mantiene los mismos parámetros SSM (*System Manager Parameter Store — almacén de configuración de AWS*) y Outputs que el resto del stack espera, por lo que el cambio fue transparente.

**Archivo modificado:** `templates/03-rds.yaml` — reescrito completamente.

---

### Error 4 — Instancia RDS `db.t3.medium` bloqueada en Free Tier

**Qué decía el error:** `DatabaseInstance CREATE_FAILED` con el mensaje: "This instance size isn't available with free plan accounts."

**Por qué ocurría:** El parámetro `DatabaseInstanceType` tenía `db.r6g.large` como default original. Se había cambiado a `db.t3.medium`, pero esta instancia tampoco está disponible en Free Tier. La única instancia RDS elegible en Free Tier es `db.t3.micro`.

**Cómo se resolvió:** Se agregó `db.t3.micro` a `AllowedValues` y se cambió el `Default` a `db.t3.micro` en ambos archivos donde aparece el parámetro.

**Archivos modificados:** `templates/00-main.yaml`, `templates/03-rds.yaml`

---

### Error 5 — Instancia EC2 `t3.medium` bloqueada en Free Tier

**Qué decía el error:** `WebAutoScalingGroup` (*grupo de autoescalado de servidores web*) nunca lanzó instancias. El ASG reintentaba silenciosamente cada ~8 minutos, sin reportar error en CloudFormation. La causa real se encontró consultando el ASG directamente por CLI: "The specified instance type is not eligible for Free Tier."

**Por qué ocurría:** El Auto Scaling Group (ASG — *servicio de AWS que lanza y gestiona servidores automáticamente*) intentaba crear instancias `t3.medium`, que no son elegibles para Free Tier. Al fallar, reintentaba sin avisar a CloudFormation, causando un stack "colgado" durante casi 1 hora.

**Cómo se resolvió:** Se usa `t3.micro` como tipo de instancia EC2, el único elegible en Free Tier. Ya estaba en `AllowedValues` del template, por lo que bastó con pasarlo como parámetro. El default se cambió en `00-main.yaml`.

**Archivo modificado:** `templates/00-main.yaml` (cambio de default en `WebInstanceType`)

---

### Error 6 — Rollback bloqueado: bucket S3 con logs del ALB

**Qué decía el error:** El sub-stack `publicalb` quedaba en `DELETE_FAILED` durante el rollback con el mensaje: "The bucket you tried to delete is not empty."

**Por qué ocurría:** El ALB (*Application Load Balancer — balanceador de carga que distribuye el tráfico web*) escribe logs de acceso en un bucket S3 automáticamente. Cuando CloudFormation intenta hacer rollback, trata de borrar ese bucket, pero falla porque S3 no permite borrar buckets con contenido.

**Cómo se resolvió:** Se vació el bucket manualmente con `aws s3 rm s3://<nombre-bucket> --recursive` antes de iniciar el delete del stack. Este procedimiento se documentó como estándar para todos los deploys.

**Archivo modificado:** Ninguno. Es un procedimiento operativo requerido antes de cada delete.

```bash
# Comando para vaciar el bucket del ALB antes de borrar el stack:
aws s3 ls | grep <nombre-del-stack>
aws s3 rm s3://<nombre-del-bucket-alb> --recursive --region us-east-1
```

---

### Error 7 — PHP `zip.so` causa segfault en instancias `t3.micro`

**Qué decía el error:** `WebAutoScalingGroup CREATE_FAILED`. En el log de la instancia EC2 (obtenido con `aws ec2 get-console-output`): `php segfault at ... in zip.so (deleted)`. Seguido de: `Error occurred during build: Command create_site_conf failed`.

**Por qué ocurría:** El script de configuración de la instancia usaba `pecl install zip` para compilar la extensión PHP de manejo de archivos ZIP. En `t3.micro` (1GB de RAM), la compilación desde código fuente requería más memoria de la disponible y el proceso del sistema operativo fallaba con un error fatal ("segmentation fault" — *acceso a memoria no permitido*).

**Cómo se resolvió:** Se instaló `php8.1-zip` directamente desde el repositorio de paquetes del sistema operativo (Amazon Linux 2023), eliminando la necesidad de compilar. Se agregó `php8.1-zip: []` al bloque de instalación de `cfn-init` (*herramienta de AWS para configurar instancias EC2*) y se eliminaron las líneas `pecl install zip` y la creación manual del archivo `50-zip.ini`.

**Archivo modificado:** `templates/04-web.yaml`

---

### Error 8 — CodeDeploy bloqueado en cuentas Free Tier

**Qué decía el error:** El sub-stack `codePipeline` → recurso `MoodleDeployApp` (`AWS::CodeDeploy::Application`) quedaba en `CREATE_FAILED` y luego en `DELETE_FAILED`. El mensaje real via CLI: "The AWS Access Key Id needs a subscription for the service (Service: CodeDeploy, Status Code: 400)."

**Por qué ocurría:** AWS CodeDeploy (*servicio de entrega automática de código a servidores*) no está disponible en cuentas Free Tier. El template original asumía que siempre estaría disponible.

**Cómo se resolvió:** Se agregó un parámetro `DeployPipeline` (tipo boolean, default `false`) en `00-main.yaml`. Se refactorizaron las condiciones `DeployUsingRDSInstances` y `DeployUsingRDSServerless` por condiciones más específicas `DeployCodePipeline` y `DeployCodePipelineServerless` que requieren tanto `DatabaseUseServerless` correcto como `DeployPipeline=true`. Con `DeployPipeline=false` (default), el sub-stack de CodePipeline/CodeDeploy no se crea.

**Archivos modificados:** `templates/00-main.yaml`

> **Implicación:** Al desactivar CodePipeline, Moodle dejaba de instalarse automáticamente (CodePipeline era el mecanismo que copiaba los archivos de Moodle al servidor). Esto generó el Error 9.

---

### Error 9 — Moodle no se instalaba: solo aparecía "It works!"

**Qué decía el error:** Stack en `CREATE_COMPLETE`, el ALB respondía, pero el navegador mostraba "It works!" (la página por defecto de Apache — *el servidor web*), no Moodle.

**Por qué ocurría:** Con CodePipeline desactivado, los archivos de Moodle nunca llegaban al directorio `/var/www/moodle/html/` del servidor EC2. La arquitectura original dependía de CodePipeline para esta tarea.

**Cómo se resolvió:** Se agregó un bloque de descarga directa en el script `create_site_conf.sh` dentro de `04-web.yaml`. El script ahora descarga Moodle directamente desde `download.moodle.org` durante el arranque de la instancia, si los archivos no están presentes. Se seleccionó Moodle 4.5 LTS (soporte hasta octubre de 2027, compatible con PHP 8.1 ya instalado en el sistema).

```bash
# Lógica agregada al script de configuración:
if [ ! -f /var/www/moodle/html/index.php ]; then
  wget -O /tmp/moodle.tgz ${MoodleDirectDownloadURL}
  tar -xvzf /tmp/moodle.tgz --strip-components=1 -C /var/www/moodle/html/
  chown -R apache:apache /var/www/moodle/html/
  rm -f /tmp/moodle.tgz
fi
```

**Archivos modificados:** `templates/04-web.yaml`, `templates/00-main.yaml`

---

### Error 10 — ASG ciclaba instancias indefinidamente (502 Bad Gateway)

**Qué decía el error:** Stack `CREATE_COMPLETE`, pero el navegador devolvía 502 (*error de puerta de enlace incorrecta*). Las instancias en el target group (*destino del balanceador de carga*) aparecían como `unhealthy`, eran terminadas y reemplazadas continuamente.

**Por qué ocurría:** El `HealthCheckGracePeriod` (*período de gracia antes de evaluar si una instancia está sana*) estaba configurado en 120 segundos. El ALB realiza 5 checks de salud con intervalos de 30 segundos, necesitando mínimo 150 segundos para aprobar una instancia. Con solo 120s de gracia, el ASG evaluaba la salud antes de que el ALB terminara su proceso, marcaba la instancia como no saludable, y la terminaba para lanzar otra.

**Cómo se resolvió:** Se cambió `HealthCheckGracePeriod` de `120` a `300` segundos en `04-web.yaml`. Esto da tiempo suficiente para que Apache arranque, cfn-init (*herramienta de configuración de instancias*) termine, y el ALB complete sus checks de salud.

**Archivo modificado:** `templates/04-web.yaml`

---

### Error 11 — Directorio raíz de Moodle sin permisos de escritura

**Qué decía el error:** El wizard de instalación web mostraba: "El directorio padre (/var/www/moodle) no tiene permisos de escritura."

**Por qué ocurría:** El directorio `/var/www/moodle` era propiedad del usuario `root`, pero Apache corre como usuario `apache`. Apache no podía escribir ahí.

**Cómo se resolvió:** Se ejecutó `chown apache:apache /var/www/moodle` y `chmod 755 /var/www/moodle` vía SSM (*Systems Manager — herramienta de AWS para ejecutar comandos en servidores sin acceso SSH*). Esta corrección se incorporó posteriormente como parte de la automatización en `04-web.yaml`.

**Archivo modificado:** `templates/04-web.yaml` (corrección incluida en la automatización posterior)

---

### Error 12 — RDS creado con PostgreSQL en lugar de MySQL

**Qué decía el error:** El wizard de instalación web no podía conectar con la base de datos. Al verificar via CLI: el RDS tenía `Engine: postgres` y puerto `5432`, pero Moodle intentaba conectar por el puerto `3306` (MySQL).

**Por qué ocurría:** El parámetro `DatabaseType` tenía `Default: PostgreSQL` en `00-main.yaml`. Al lanzar el stack sin especificar ese parámetro, el RDS se creó con PostgreSQL en lugar de MySQL. El security group (*regla de firewall en AWS*) de la base de datos solo tenía abierto el puerto 5432, bloqueando cualquier conexión MySQL al 3306.

**Cómo se resolvió:** Se cambió `Default: PostgreSQL` a `Default: MySQL` en el parámetro `DatabaseType` de `00-main.yaml`.

**Archivo modificado:** `templates/00-main.yaml`

> **Discrepancia detectada en el código actual:** `03-rds.yaml` aún muestra `Default: PostgreSQL` en su propio parámetro `DatabaseType`. Sin embargo, esto no afecta el comportamiento real: cuando se despliega a través de `00-main.yaml`, el valor `MySQL` se pasa explícitamente al sub-stack, sobreescribiendo ese default. El default en `03-rds.yaml` solo aplica si alguien desplegara ese template de forma aislada, fuera del stack principal.

---

### Error 13 — MySQL 8.0.39 retirada por AWS

**Qué decía el error:** `DatabaseInstance CREATE_FAILED`. AWS ya no ofrecía la versión `8.0.39` de MySQL.

**Por qué ocurría:** AWS retira periódicamente versiones de motores de base de datos. La versión `8.0.39` había sido eliminada del catálogo de RDS.

**Cómo se resolvió:** Se actualizó la `EngineVersion` a `'8.0.45'` en `03-rds.yaml`, que era la versión disponible al momento de la corrección.

**Archivo modificado:** `templates/03-rds.yaml`

> **Nota para el futuro:** Si el deploy falla por `EngineVersion` inválida, verificar las versiones disponibles con: `aws rds describe-db-engine-versions --engine mysql --query "DBEngineVersions[*].EngineVersion" --output table --region us-east-1`

---

### Error 14 — YAML mal formado por scripts PHP en bloques `!Sub`

**Qué decía el error:** `webapp CREATE_FAILED — Template format error: YAML not well-formed (line 676, column 1)`.

**Por qué ocurría:** El script de configuración incluía código PHP dentro de un bloque `content: !Sub |` de cfn-init. CloudFormation procesa `!Sub` para sustituir variables, pero el parser YAML interpreta líneas que empiezan en la columna 0 como fin del bloque literal. El carácter `$` en el código PHP también conflictuaba con el procesamiento de `!Sub`.

**Cómo se resolvió:** Los scripts PHP se movieron a archivos cfn-init separados usando `content: |` (sin `!Sub`). Estos archivos no son procesados por el sustituyente de variables de CloudFormation, por lo que los `$` de PHP no causan conflicto.

**Archivo modificado:** `templates/04-web.yaml`

---

### Error 15 — "Database tables already present" en segunda instancia del ASG

**Qué decía el error:** Log de cfn-init en la segunda instancia: "Database tables already present; CLI installation cannot continue."

**Por qué ocurría:** El ASG lanzó una segunda instancia de reemplazo porque la primera tardó más de `HealthCheckGracePeriod` en completar cfn-init (la compilación de extensiones PECL tardaba ~15 minutos). La segunda instancia no tenía `config.php` localmente, pero la base de datos ya tenía las ~400 tablas instaladas por la primera. El instalador CLI de Moodle rechaza instalarse sobre una base de datos con datos existentes.

**Cómo se resolvió:** Se agregó lógica de detección al script: si la base de datos ya tiene más de 10 tablas `mdl_`, se regenera `config.php` localmente (sin reinstalar) usando un script PHP auxiliar (`gen_moodle_config.php`). Esto permite que múltiples instancias arranquen correctamente cuando la BD ya está inicializada.

**Archivo modificado:** `templates/04-web.yaml`

---

## 4. Cambios realizados al código

### `templates/00-main.yaml`

| Aspecto | Antes | Después |
|---|---|---|
| `DatabaseType` default | `PostgreSQL` | `MySQL` |
| `DatabaseUseServerless` default | `true` | `false` |
| `DatabaseInstanceType` default | `db.r6g.large` | `db.t3.micro` |
| `UseServerlessSessionCache` default | `true` | `false` |
| `UseServerlessApplicationCache` default | `true` | `false` |
| `SessionCacheNodeType` default | `cache.r6g.large` | `cache.t3.micro` |
| `ApplicationCacheNodeType` default | `cache.r6g.large` | `cache.t3.micro` |
| `WebInstanceType` default | `c7g.xlarge` | `t3.micro` |
| `MoodleDirectDownloadURL` default | Moodle 4.4 (sin soporte) | Moodle 4.5 LTS |
| `DeploymentLocation` default | URL oficial AWS | Placeholder `https://<YOUR_BUCKET_NAME>.s3.<YOUR_REGION>.amazonaws.com/templates` |
| `MoodleAdminPassword` default | `MoodleAdmin1!` | `<YOUR_ADMIN_PASSWORD>` |
| `MoodleAdminEmail` default | (dirección real) | `<YOUR_ADMIN_EMAIL>` |
| Parámetro `DeployPipeline` | No existía | Agregado, default `false` |
| Parámetros `MoodleAdminUser`, `MoodleAdminPassword`, `MoodleAdminEmail` | No existían | Agregados y pasados al sub-stack `webapp` |
| Parámetro `PublicAlbDnsName` en `webapp` | No existía | Agregado (necesario para construir `wwwroot` de Moodle) |
| Condiciones de CodePipeline | `DeployUsingRDSInstances` / `DeployUsingRDSServerless` | `DeployCodePipeline` / `DeployCodePipelineServerless` (requieren `DeployPipeline=true`) |
| `ExcludeCharacters` en secret RDS | `'"@/\'` | `'"@/\$\`` ` (excluye `$` y backticks que rompían el script bash) |

**Por qué:** Este archivo es el punto de entrada de todo el deploy. Centralizar los defaults correctos aquí evita que el usuario tenga que recordar decenas de parámetros cada vez que lanza el stack.

---

### `templates/03-rds.yaml`

| Aspecto | Antes | Después |
|---|---|---|
| Tipo de recurso de BD | `AWS::RDS::DBCluster` + `AWS::RDS::DBInstance` (Aurora) | Un solo `AWS::RDS::DBInstance` (RDS estándar) |
| `DatabaseInstanceType` en `AllowedValues` | No incluía `db.t3.micro` | Incluye `db.t3.micro` como primera opción y default |
| `EngineVersion` MySQL | `8.0.39` (retirada) | `8.0.45` |
| `EngineVersion` PostgreSQL | No especificada | `16.3` |
| SSM Parameters generados | Endpoint del cluster Aurora | Endpoint de la instancia RDS (mismo valor en lectura/escritura) |
| Outputs | Orientados a Aurora | Adaptados para instancia única; `DatabaseInstance0` y `DatabaseInstance1` apuntan al mismo recurso |

**Por qué:** Aurora está bloqueado en cuentas Free Tier. RDS estándar con MySQL tiene el mismo comportamiento funcional para Moodle y es compatible con la capa gratuita. Los SSM Parameters y Outputs se mantuvieron con los mismos nombres para no romper el resto del stack.

---

### `templates/03-rdsserverless.yaml`

| Aspecto | Antes | Después |
|---|---|---|
| `EngineVersion` en `DatabaseCluster` | No especificada | `!If [ UsePostgreSQL, '16.4', '8.0.mysql_aurora.3.08.0' ]` |

**Por qué:** Sin `EngineVersion`, AWS rechazaba silenciosamente la creación del cluster. Este fix es correcto pero en la práctica no se usa cuando `DatabaseUseServerless=false` (que es el default recomendado para Free Tier).

---

### `templates/03-elasticache.yaml`

| Aspecto | Antes | Después |
|---|---|---|
| `AZMode` en cluster Memcached | `cross-az` (hardcodeado) | `!If [ NumberOfSubnets1, single-az, cross-az ]` |
| Default de `ElastiCacheNodeType` | `cache.r6g.large` | `cache.t3.micro` (en `00-main.yaml` que lo controla) |

**Por qué:** Con `NumberOfAZs=1`, el modo `cross-az` (que distribuye nodos en múltiples zonas) era inválido. El condicional selecciona `single-az` cuando hay una sola subred, evitando el error.

---

### `templates/04-web.yaml`

| Aspecto | Antes | Después |
|---|---|---|
| `HealthCheckGracePeriod` del ASG | `120` segundos | `300` segundos |
| `CreationPolicy.Timeout` | `PT15M` (15 minutos) | `PT60M` (60 minutos) |
| Instalación de extensión ZIP de PHP | `pecl install zip` (compilación desde fuente) | `php8.1-zip: []` (paquete del sistema, sin compilar) |
| Descarga de Moodle | No existía (dependía de CodePipeline) | Descarga directa desde `download.moodle.org` si no hay archivos |
| Instalación automática de Moodle | No existía | Script `admin/cli/install.php` ejecutado por cfn-init |
| Manejo de segunda instancia ASG | No existía (fallaba) | Detección de tablas en BD → regenerar `config.php` sin reinstalar |
| Scripts PHP en cfn-init | Dentro de bloques `!Sub` (rompía YAML) | Archivos separados `fix_moodle_post.php` y `gen_moodle_config.php` con `content: \|` |
| `--wwwroot` al instalar Moodle | No aplicaba | Construido en minúsculas con `tr '[:upper:]' '[:lower:]'` |
| `cookiesecure` post-instalación | No corregido | Se establece a `0` automáticamente (el site usa HTTP, no HTTPS) |
| Nuevos parámetros | N/A | `PublicAlbDnsName`, `DatabaseName`, `DatabaseType`, `MoodleAdminUser`, `MoodleAdminPassword`, `MoodleAdminEmail`, `MoodleLocale`, `MoodleDirectDownloadURL` |

**Por qué:** Este archivo controla todo lo que ocurre cuando arranca un servidor EC2. Los cambios fueron necesarios para:
1. Que la instancia pudiera iniciarse correctamente en `t3.micro` (sin segfault de zip.so)
2. Que Moodle se instalara solo, sin intervención manual ni CodePipeline
3. Que el ASG pudiera gestionar múltiples instancias sin conflictos de instalación
4. Que CloudFormation esperara el tiempo suficiente para que todo terminara

---

## 5. Limitaciones de AWS Free Tier descubiertas

Las siguientes restricciones **no están documentadas en el repositorio original** y causan fallos silenciosos o con mensajes de error poco claros:

| # | Servicio / Recurso | Restricción | Error observado |
|---|---|---|---|
| 1 | **Aurora RDS** (cualquier modo) | Completamente bloqueado. Devuelve `FreeTierRestrictionError` que CloudFormation **no muestra** en su consola — solo visible via AWS CLI con `describe-stack-events`. | `DatabaseCluster CREATE_FAILED` sin razón aparente |
| 2 | **RDS instancias** | Solo `db.t3.micro` está permitido. `db.t3.medium` y superiores devuelven error explícito. | "This instance size isn't available with free plan accounts." |
| 3 | **EC2 instancias** | Solo `t3.micro` (y equivalentes de capa gratuita) son elegibles. El Auto Scaling Group falla silenciosamente, reintentando cada ~8 minutos sin reportar el error a CloudFormation. | Stack "colgado" durante 45-60 min sin mensaje de error |
| 4 | **ElastiCache Serverless** | `AWS::ElastiCache::ServerlessCache` solo acepta `redis` o `valkey`, no `Memcached`. El template original usa Memcached por defecto. | `sessioncache CREATE_FAILED` |
| 5 | **AWS CodeDeploy** | No disponible en cuentas Free Tier. Devuelve HTTP 400 con "needs a subscription". El recurso también queda en `DELETE_FAILED` durante el rollback (requiere `--retain-resources`). | `MoodleDeployApp CREATE_FAILED` y luego `DELETE_FAILED` |
| 6 | **Compilación de extensiones PHP** en `t3.micro` | La instancia `t3.micro` tiene 1GB de RAM. Compilar extensiones PHP con `pecl install` puede causar `segfault` por falta de memoria. | `zip.so segfault` → `create_site_conf failed` |
| 7 | **Versiones de motor RDS** | AWS retira versiones de motores periódicamente. `MySQL 8.0.39` fue retirada. Los templates con versiones hardcodeadas dejan de funcionar sin aviso previo. | `EngineVersion invalid` |
| 8 | **Bucket S3 del ALB con logs** | CloudFormation no puede borrar un bucket S3 con contenido. El ALB escribe logs continuamente, por lo que el bucket siempre tiene datos al momento del rollback. Bloquea cualquier delete del stack. | `publicalb DELETE_FAILED` |

---

## 6. Resultado final

### Estado del deploy

Tras 14 intentos de deploy (NewMoodle1 a NewMoodle14) y correcciones acumuladas en cada sesión, se logró que el stack llegara a `CREATE_COMPLETE` con Moodle 4.5 LTS completamente instalado y operativo.

El stack NewMoodle14 fue el primero en lograr la **instalación completamente automatizada**: al lanzar el stack con los parámetros correctos, Moodle queda instalado y accesible sin ninguna intervención manual.

### Qué se creó en AWS

| Recurso | Tipo | Descripción |
|---|---|---|
| VPC | Red privada virtual | Aísla todos los recursos del proyecto |
| Subredes públicas (x2) | Redes dentro de la VPC | Para el balanceador de carga |
| Subredes de aplicación (x2) | Redes dentro de la VPC | Para los servidores EC2 |
| Subredes de datos (x2) | Redes dentro de la VPC | Para RDS y ElastiCache |
| RDS MySQL 8.0.45 | Base de datos | Motor de persistencia de Moodle |
| ElastiCache Memcached | Caché | Almacena sesiones y contenido en memoria |
| EFS | Sistema de archivos | Almacenamiento compartido para archivos de Moodle |
| ALB | Balanceador de carga | Distribuye el tráfico HTTP hacia los servidores |
| Auto Scaling Group | Gestión de EC2 | Lanza y mantiene la instancia EC2 |
| EC2 `t3.micro` | Servidor web | Corre Apache + PHP + Moodle |

### Cómo acceder a Moodle

La URL de acceso sigue el patrón del DNS del ALB. Se obtiene con:

```bash
aws ssm get-parameter \
  --name "/Moodle/<NOMBRE_DEL_STACK>/Network/DomainName" \
  --region us-east-1 \
  --query "Parameter.Value" \
  --output text
```

### Credenciales de ejemplo (del deploy NewMoodle14)

> **Importante:** Estas credenciales son de un stack de prueba que fue eliminado. No están activas. Sirven como referencia del formato.

| Campo | Valor de ejemplo |
|---|---|
| URL | `http://<alb-dns>.us-east-1.elb.amazonaws.com` |
| Usuario admin | `admin` (o el valor de `MoodleAdminUser`) |
| Contraseña admin | El valor que se pasó en `MoodleAdminPassword` al crear el stack |

---

## 7. Guía para reproducirlo

Esta guía permite a cualquier persona desplegar Moodle en una cuenta AWS Free Tier desde cero.

### Requisitos previos

- Cuenta AWS activa (Free Tier o superior)
- AWS CLI instalado y configurado con credenciales válidas
- Git instalado

### Paso 1 — Clonar el repositorio

```bash
git clone https://github.com/<tu-usuario>/aws-refarch-moodle.git
cd aws-refarch-moodle
```

### Paso 2 — Crear un bucket S3 para los templates

CloudFormation necesita que los templates estén accesibles en S3. Crea un bucket propio:

```bash
# Elige un nombre único para tu bucket (no puede repetirse globalmente en AWS)
BUCKET_NAME="moodle-templates-$(date +%s)"
REGION="us-east-1"

aws s3 mb s3://$BUCKET_NAME --region $REGION

# Subir todos los templates
aws s3 cp templates/ s3://$BUCKET_NAME/templates/ --recursive --region $REGION

echo "URL de tus templates: https://$BUCKET_NAME.s3.$REGION.amazonaws.com/templates"
```

### Paso 3 — Lanzar el stack en CloudFormation

Abre la consola de AWS → CloudFormation → "Create stack" → "With new resources".

**Template URL:**
```
https://<TU_BUCKET>.s3.us-east-1.amazonaws.com/templates/00-main.yaml
```

**Parámetros que DEBES completar (los demás tienen defaults correctos):**

| Parámetro | Valor a ingresar |
|---|---|
| `DeploymentLocation` | `https://<TU_BUCKET>.s3.us-east-1.amazonaws.com/templates` |
| `AvailabilityZones` | Seleccionar: `us-east-1a` y `us-east-1b` |
| `NotifyEmailAddress` | Tu email real |
| `MoodleAdminPassword` | Contraseña segura (mínimo 8 caracteres, sin espacios, `$`, comillas ni barras invertidas) |
| `MoodleAdminEmail` | Tu email para la cuenta admin de Moodle |

**Parámetros con defaults ya correctos para Free Tier (no tocar):**

| Parámetro | Default configurado | Por qué es así |
|---|---|---|
| `DatabaseType` | `MySQL` | MySQL funciona con Free Tier; PostgreSQL tiene menos soporte en Moodle |
| `DatabaseUseServerless` | `false` | Aurora Serverless está bloqueado en Free Tier |
| `DatabaseInstanceType` | `db.t3.micro` | Única instancia RDS elegible en Free Tier |
| `UseServerlessSessionCache` | `false` | ElastiCache Serverless no soporta Memcached |
| `UseServerlessApplicationCache` | `false` | Ídem |
| `SessionCacheNodeType` | `cache.t3.micro` | Más económico disponible |
| `ApplicationCacheNodeType` | `cache.t3.micro` | Ídem |
| `WebInstanceType` | `t3.micro` | Único tipo EC2 elegible en Free Tier |
| `DeployPipeline` | `false` | CodeDeploy está bloqueado en Free Tier |
| `NumberOfAZs` | `2` | Mínimo requerido por RDS y ElastiCache |

### Paso 4 — Monitorear el progreso

El stack puede tardar entre 30 y 50 minutos. Se puede monitorear en la consola de CloudFormation o via CLI:

```bash
# Ver estado del stack principal
aws cloudformation describe-stacks \
  --stack-name <NOMBRE_DEL_STACK> \
  --region us-east-1 \
  --query "Stacks[0].StackStatus" \
  --output text
```

El orden esperado de creación de sub-stacks:

1. `vpc` → `CREATE_COMPLETE`
2. `securitygroups` → `CREATE_COMPLETE`
3. `rds`, `sessioncache`, `sharedEFS`, `publicalb`, `pipelineHelper` → en paralelo
4. `webapp` → `CREATE_COMPLETE` (tarda más, incluye descarga e instalación de Moodle)

### Paso 5 — Obtener la URL de Moodle

Una vez el stack esté en `CREATE_COMPLETE`:

```bash
aws ssm get-parameter \
  --name "/Moodle/<NOMBRE_DEL_STACK>/Network/DomainName" \
  --region us-east-1 \
  --query "Parameter.Value" \
  --output text
```

Abrir esa URL en el navegador. Moodle debe estar instalado y listo para el login.

### Paso 6 — Primer login

- **Usuario:** el valor de `MoodleAdminUser` (default: `admin`)
- **Contraseña:** el valor de `MoodleAdminPassword` que ingresaste en el Paso 3

Se recomienda cambiar la contraseña en el primer login desde **Administración del sitio → Usuarios → Cuentas → Perfiles de usuario**.

### Paso 7 — Eliminar el stack (cuando ya no se necesite)

Para evitar costos, eliminar el stack cuando no esté en uso. **Antes de borrarlo**, vaciar el bucket del ALB:

```bash
# 1. Encontrar el bucket del ALB
aws s3 ls | grep <nombre-del-stack-en-minúsculas>
# Buscar el bucket que dice "loadbalanceraccesslogs"

# 2. Vaciarlo
aws s3 rm s3://<nombre-del-bucket-alb> --recursive --region us-east-1

# 3. También vaciar los buckets del pipelineHelper si existen
aws s3 rm s3://<nombre-bucket-codeartifacts> --recursive --region us-east-1
aws s3 rm s3://<nombre-bucket-moodlegit> --recursive --region us-east-1

# 4. Eliminar el stack
aws cloudformation delete-stack --stack-name <NOMBRE_DEL_STACK> --region us-east-1
```

> **Por qué hay que vaciar el bucket primero:** CloudFormation no puede borrar buckets S3 con contenido. El ALB escribe logs de acceso constantemente, por lo que el bucket siempre tiene archivos. Si no se vacía antes, el sub-stack `publicalb` queda en `DELETE_FAILED` y hay que limpiarlo manualmente.

### Procedimiento para limpiar un stack en DELETE_FAILED

Si el stack queda en `ROLLBACK_FAILED` o `DELETE_FAILED`:

```bash
# Si el sub-stack de publicalb quedó bloqueado:
# 1. Vaciar el bucket (ver Paso 7)
# 2. Reintentar el delete del stack padre:
aws cloudformation delete-stack --stack-name <NOMBRE_DEL_STACK> --region us-east-1

# Si el sub-stack de codePipeline quedó en DELETE_FAILED por MoodleDeployApp:
aws cloudformation delete-stack \
  --stack-name <STACK>-codePipeline-XXXX \
  --retain-resources MoodleDeployApp \
  --region us-east-1
# Luego eliminar el stack padre normalmente
```

---

## Apéndice: Historial completo de stacks intentados

| Stack | Fecha | Resultado | Causa raíz |
|---|---|---|---|
| Stack original | Antes 2026-03-23 | FAILED | `NumberOfAZs=1` — menos de 2 zonas de disponibilidad |
| NewMoodle | 2026-03-24 | ROLLBACK_FAILED | `EngineVersion` faltaba en Aurora + `sessioncache` DELETE_FAILED |
| NewMoodle2 | 2026-03-24 | ROLLBACK | `EngineVersion=15.4` no existe en us-east-1 |
| NewMoodle3 | 2026-03-24 | ROLLBACK | Aurora bloqueado en Free Tier (error invisible en consola) |
| NewMoodle4 | 2026-03-26 | ROLLBACK | `db.t3.medium` no disponible en Free Tier |
| NewMoodle5 | 2026-03-26 | ROLLBACK_FAILED | `t3.medium` en EC2 no elegible en Free Tier; bucket ALB bloqueó delete |
| NewMoodle6 | 2026-03-26 | ROLLBACK | `zip.so` segfault por falta de RAM en `t3.micro` |
| NewMoodle7 | 2026-03-26 | ROLLBACK_FAILED | CodeDeploy bloqueado en Free Tier + DELETE_FAILED en rollback |
| NewMoodle8 | 2026-03-26 | CREATE_COMPLETE | Stack completo pero sin Moodle — CodePipeline desactivado sin reemplazo |
| NewMoodle9 | 2026-03-26/04-01 | ELIMINADO | RDS creado con PostgreSQL por default incorrecto; wizard web falló |
| NewMoodle10 | 2026-04-01 | ROLLBACK | MySQL 8.0.39 retirada por AWS |
| NewMoodle11 | 2026-04-01 | CREATE_COMPLETE ✓ | Moodle instalado manualmente via CLI/SSM (no automatizado) |
| NewMoodle12 | 2026-04-02 | ROLLBACK | YAML mal formado — scripts PHP con `$` en columna 0 dentro de `!Sub` |
| NewMoodle13 | 2026-04-02 | ROLLBACK | "DB tables already present" — segunda instancia ASG sin `config.php` |
| **NewMoodle14** | **2026-04-02** | **CREATE_COMPLETE ✓** | **Instalación completamente automatizada — Moodle operativo** |
