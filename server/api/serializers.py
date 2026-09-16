from django.conf import settings
from rest_framework import serializers

from engine.params import params_from_dict

from .models import Dataset, MarkerGene, Run

ALLOWED_SUFFIXES = (".h5ad", ".tar.gz", ".tgz")


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "original_filename", "size_bytes", "status",
                  "n_cells", "n_genes", "error", "created_at"]
        read_only_fields = fields


class DatasetUploadSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    file = serializers.FileField()

    def validate_file(self, f):
        if not f.name.lower().endswith(ALLOWED_SUFFIXES):
            raise serializers.ValidationError("Upload a .h5ad file or a 10x .tar.gz bundle.")
        if f.size > settings.MAX_UPLOAD_BYTES:
            raise serializers.ValidationError(
                f"File is {f.size/1e6:.0f} MB; limit is {settings.MAX_UPLOAD_BYTES/1e6:.0f} MB.")
        return f


class RunSerializer(serializers.ModelSerializer):
    dataset = serializers.PrimaryKeyRelatedField(queryset=Dataset.objects.none())
    params = serializers.JSONField(required=False)

    class Meta:
        model = Run
        fields = ["id", "dataset", "params", "job_id", "preprocess_key", "status", "cache_hit",
                  "n_clusters", "timings", "error", "created_at", "started_at", "finished_at"]
        read_only_fields = ["id", "job_id", "preprocess_key", "status", "cache_hit", "n_clusters",
                            "timings", "error", "created_at", "started_at", "finished_at"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        if request is not None:
            # Users can only run against their own, validated datasets.
            self.fields["dataset"].queryset = Dataset.objects.filter(
                owner=request.user, status=Dataset.Status.READY)

    def validate_params(self, p):
        if not isinstance(p, dict):
            raise serializers.ValidationError("params must be an object")
        try:
            params_from_dict(p)          # rejects unknown keys and wrong types
        except (TypeError, ValueError) as e:
            raise serializers.ValidationError(str(e))
        return p


class MarkerGeneSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarkerGene
        fields = ["cluster_id", "rank", "gene", "score", "log2fc", "pct_in", "pct_out", "pval", "padj"]
