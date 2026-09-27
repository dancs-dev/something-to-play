import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.state import StateApps


def move_subjects_to_games(
    apps: StateApps, schema_editor: BaseDatabaseSchemaEditor
) -> None:
    Game = apps.get_model("app", "Game")
    Preference = apps.get_model("app", "Preference")
    games = {}
    for preference in Preference.objects.using(schema_editor.connection.alias).order_by(
        "pk"
    ):
        key = preference.subject
        if key not in games:
            games[key] = (
                Game.objects.using(schema_editor.connection.alias)
                .create(title=preference.subject)
                .pk
            )
        preference.game_id = games[key]
        preference.save(using=schema_editor.connection.alias, update_fields=["game"])


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0004_simplify_app"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Game",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name="CatalogueState",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("provider", models.CharField(max_length=30, unique=True)),
                ("last_synced_at", models.DateTimeField()),
            ],
        ),
        migrations.CreateModel(
            name="GameIdentity",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("provider", models.CharField(max_length=30)),
                ("external_id", models.CharField(max_length=64)),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="identities",
                        to="app.game",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="LinkedAccount",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("provider", models.CharField(max_length=30)),
                ("external_user_id", models.CharField(max_length=64)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Ownership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="app.linkedaccount",
                    ),
                ),
                (
                    "game",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, to="app.game"
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="preference",
            name="game",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.CASCADE, to="app.game"
            ),
        ),
        migrations.RunPython(move_subjects_to_games, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="preference", name="unique_user_taste"),
        migrations.RemoveField(model_name="preference", name="subject"),
        migrations.AlterField(
            model_name="preference",
            name="game",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, to="app.game"
            ),
        ),
        migrations.AlterField(
            model_name="preference",
            name="sentiment",
            field=models.SmallIntegerField(
                choices=[(1, "Like"), (-1, "Dislike"), (0, "Ignore")]
            ),
        ),
        migrations.AddConstraint(
            model_name="preference",
            constraint=models.UniqueConstraint(
                fields=("user", "game"), name="unique_user_taste"
            ),
        ),
        migrations.AddConstraint(
            model_name="gameidentity",
            constraint=models.UniqueConstraint(
                fields=("provider", "external_id"), name="unique_game_identity"
            ),
        ),
        migrations.AddConstraint(
            model_name="linkedaccount",
            constraint=models.UniqueConstraint(
                fields=("user", "provider"), name="unique_user_provider"
            ),
        ),
        migrations.AddConstraint(
            model_name="ownership",
            constraint=models.UniqueConstraint(
                fields=("account", "game"), name="unique_account_game"
            ),
        ),
    ]
