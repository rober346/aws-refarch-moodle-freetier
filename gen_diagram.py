"""
Genera arquitectura-moodle-freetier.png
Layout: TB — Internet/usuarios arriba, flujo hacia abajo,
        Managed Services a la derecha (fuera de VPC)
"""
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EC2
from diagrams.aws.database import RDS, ElastiCache
from diagrams.aws.network import ELB, NATGateway, InternetGateway
from diagrams.aws.storage import EFS, S3
from diagrams.aws.management import Cloudwatch, SystemsManager
from diagrams.aws.security import SecretsManager
from diagrams.onprem.client import Users

graph_attr = {
    "fontsize": "20",
    "bgcolor": "white",
    "pad": "1.0",
    "splines": "polyline",
    "nodesep": "0.7",
    "ranksep": "1.0",
    "fontname": "Arial",
    "dpi": "180",
    "concentrate": "false",
}

def C(bgcolor, pencolor, title_size="14"):
    return {
        "bgcolor": bgcolor,
        "style": "rounded",
        "penwidth": "2",
        "pencolor": pencolor,
        "fontsize": title_size,
        "fontname": "Arial Bold",
        "margin": "18",
    }

def CD(bgcolor, pencolor, title_size="13"):
    return {
        "bgcolor": bgcolor,
        "style": "dashed",
        "penwidth": "1.5",
        "pencolor": pencolor,
        "fontsize": title_size,
        "fontname": "Arial",
        "margin": "15",
    }

with Diagram(
    "Moodle Free Tier — AWS (us-east-1)",
    filename="arquitectura-moodle-freetier",
    outformat="png",
    graph_attr=graph_attr,
    direction="TB",
    show=False,
):

    users = Users("Internet\nUsers")
    igw   = InternetGateway("Internet Gateway")

    # ── Managed services (right column) ────────────────────────────────────
    with Cluster("AWS Managed Services", graph_attr=C("#F5EEF8", "#8E44AD", "13")):
        sm  = SecretsManager("Secrets Manager\n(RDS credentials)")
        ssm = SystemsManager("SSM Parameter Store\n(endpoints, config)")
        cw  = Cloudwatch("CloudWatch\nLogs & Alarms")

    # ── VPC ────────────────────────────────────────────────────────────────
    with Cluster("VPC  10.0.0.0/16  |  us-east-1  |  2 AZs", graph_attr=C("#EBF5FB", "#2E86C1", "15")):

        # Public
        with Cluster("Public Subnets\n10.0.200.0/24  ·  10.0.201.0/24", graph_attr=CD("#FEF9E7", "#F39C12")):
            alb = ELB("Application Load Balancer\n(internet-facing · HTTP :80)")
            with Cluster("us-east-1a", graph_attr=CD("transparent", "#ABB2B9", "11")):
                nat_a = NATGateway("NAT GW\n(AZ-a)")
            with Cluster("us-east-1b", graph_attr=CD("transparent", "#ABB2B9", "11")):
                nat_b = NATGateway("NAT GW\n(AZ-b)")

        # App
        with Cluster("App Subnets — Private\n10.0.0.0/22  ·  10.0.4.0/22", graph_attr=CD("#EAFAF1", "#27AE60")):
            with Cluster("Auto Scaling Group  (Min: 1  /  Max: 1)", graph_attr=CD("#FDF2E9", "#E67E22", "12")):
                with Cluster("us-east-1a", graph_attr=CD("transparent", "#ABB2B9", "11")):
                    ec2_a = EC2("Moodle App Server\nt3.micro · AL2023\nApache + PHP 8.1")
                with Cluster("us-east-1b", graph_attr=CD("transparent", "#ABB2B9", "11")):
                    ec2_b = EC2("Moodle App Server\nt3.micro · AL2023\n(scale-out)")
            s3 = S3("S3 Bucket\n(ALB logs / artefactos)")

        # Data
        with Cluster("Data Subnets — Private\n10.0.100.0/24  ·  10.0.101.0/24", graph_attr=CD("#FDEDEC", "#E74C3C")):
            with Cluster("us-east-1a", graph_attr=CD("transparent", "#ABB2B9", "11")):
                rds     = RDS("RDS MySQL 8.0.45\ndb.t3.micro · 20 GB gp2\ncifrado · Single-AZ")
                cache_a = ElastiCache("ElastiCache Memcached\ncache.t3.micro")
            with Cluster("us-east-1b", graph_attr=CD("transparent", "#ABB2B9", "11")):
                efs     = EFS("Amazon EFS\nmoodledata compartido\n/var/www/moodle/data")
                cache_b = ElastiCache("ElastiCache Memcached\ncache.t3.micro")

    # ── Edges ───────────────────────────────────────────────────────────────

    # Internet → IGW → ALB
    users >> Edge(label="HTTP / HTTPS", fontsize="11") >> igw
    igw   >> Edge(fontsize="11") >> alb

    # ALB → EC2
    alb >> Edge(label="HTTP :80", color="#2980B9", fontsize="11") >> ec2_a
    alb >> Edge(label="HTTP :80", color="#2980B9", style="dashed", fontsize="11") >> ec2_b

    # EC2 → NAT (egress)
    ec2_a >> Edge(label="egress", style="dashed", color="#95A5A6", fontsize="10") >> nat_a
    ec2_b >> Edge(label="egress", style="dashed", color="#95A5A6", fontsize="10") >> nat_b

    # EC2 → RDS
    ec2_a >> Edge(label="MySQL :3306", color="#2471A3", fontsize="11") >> rds
    ec2_b >> Edge(label="MySQL :3306", color="#2471A3", style="dashed", fontsize="11") >> rds

    # EC2 → ElastiCache
    ec2_a >> Edge(label="Memcached :11211", color="#1E8449", fontsize="11") >> cache_a
    ec2_b >> Edge(label="Memcached :11211", color="#1E8449", style="dashed", fontsize="11") >> cache_b

    # EC2 → EFS
    ec2_a >> Edge(label="NFS :2049", color="#7D3C98", fontsize="11") >> efs
    ec2_b >> Edge(label="NFS :2049", color="#7D3C98", style="dashed", fontsize="11") >> efs

    # EC2 → S3
    ec2_a >> Edge(style="dashed", color="#95A5A6", fontsize="10") >> s3

    # EC2 → Managed services
    ec2_a >> Edge(label="GetSecretValue", color="#7D3C98", fontsize="10") >> sm
    ec2_a >> Edge(label="GetParameter",   color="#7D3C98", fontsize="10") >> ssm
    ec2_a >> Edge(label="PutLogEvents",   color="#7D3C98", fontsize="10") >> cw
