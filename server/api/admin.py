from django.contrib import admin

from .models import Dataset, MarkerGene, Run


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "status", "n_cells", "n_genes", "created_at")


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("id", "dataset", "status", "cache_hit", "n_clusters", "created_at")


@admin.register(MarkerGene)
class MarkerGeneAdmin(admin.ModelAdmin):
    list_display = ("run", "cluster_id", "rank", "gene", "score", "padj")
