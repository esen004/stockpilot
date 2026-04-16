from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="VintEdgeSubscriber",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(db_index=True, max_length=254, unique=True)),
                ("stripe_customer_id", models.CharField(blank=True, default="", max_length=255)),
                ("stripe_subscription_id", models.CharField(blank=True, default="", max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("plan", models.CharField(default="pro", max_length=50)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "verbose_name": "VintEdge Subscriber",
                "verbose_name_plural": "VintEdge Subscribers",
            },
        ),
    ]
