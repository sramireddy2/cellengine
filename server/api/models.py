"""Relational data: the rigid, queryable half of the system.

    Dataset      one uploaded count matrix
    Run          one (dataset, parameter set) execution, with status + timings
    CellCluster  one row per cell per run: cluster id + UMAP coordinates
    MarkerGene   top genes per cluster per run

CellCluster and MarkerGene rows for a run are written in one transaction each
(see jobs.py). A run that shows 60% of its cells is worse than no run.
"""
import uuid

from django.conf import settings
from django.db import models


class Dataset(models.Model):
    class Status(models.TextChoices):
        UPLOADED = "uploaded"      # file on disk, not yet opened
        READY = "ready"            # worker validated it and recorded its shape
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="datasets")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to="datasets/%Y/%m/")
    original_filename = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UPLOADED)
    n_cells = models.IntegerField(null=True, blank=True)
    n_genes = models.IntegerField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.status})"


class Run(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued"
        PREPROCESSING = "preprocessing"   # computing or loading the cached graph
        CLUSTERING = "clustering"         # Leiden
        MARKERS = "markers"               # labels are committed; scatter can render
        DONE = "done"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="runs")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="runs")
    params = models.JSONField(default=dict)              # flat dict, see engine.params.params_from_dict
    preprocess_key = models.CharField(max_length=80, blank=True, default="", db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    cache_hit = models.BooleanField(null=True)           # did the graph come from Redis?
    n_clusters = models.IntegerField(null=True, blank=True)
    timings = models.JSONField(default=dict)             # seconds per stage
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"run {self.id} [{self.status}]"


class CellCluster(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="cells")
    cell_barcode = models.CharField(max_length=64)
    cluster_id = models.PositiveSmallIntegerField()
    umap_x = models.FloatField()
    umap_y = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "cell_barcode"], name="uniq_run_barcode")]
        indexes = [models.Index(fields=["run", "cluster_id"])]


class MarkerGene(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="markers")
    cluster_id = models.PositiveSmallIntegerField()
    rank = models.PositiveSmallIntegerField()
    gene = models.CharField(max_length=64)
    score = models.FloatField()        # Wilcoxon z
    log2fc = models.FloatField()
    pct_in = models.FloatField()
    pct_out = models.FloatField()
    pval = models.FloatField()
    padj = models.FloatField()

    class Meta:
        ordering = ["cluster_id", "rank"]
        indexes = [models.Index(fields=["run", "cluster_id"])]
