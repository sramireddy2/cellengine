from django.contrib.auth import authenticate, get_user_model, login, logout
from django.db.models import Count
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from . import jobs
from .infra import enqueue
from .models import Dataset, MarkerGene, Run
from .serializers import DatasetSerializer, DatasetUploadSerializer, MarkerGeneSerializer, RunSerializer

User = get_user_model()


# --- health -------------------------------------------------------------------

@api_view(["GET"])
@permission_classes([AllowAny])
def healthz(request):
    """Liveness/readiness: can we reach the database? (Redis is the worker's problem.)"""
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return Response({"ok": True})


# --- auth (session based; the frontend is same-origin) ------------------------

@api_view(["POST"])
@permission_classes([AllowAny])
def login_view(request):
    user = authenticate(request, username=request.data.get("username", ""),
                        password=request.data.get("password", ""))
    if user is None:
        return Response({"detail": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
    login(request, user)
    return Response({"username": user.username})


@api_view(["POST"])
@permission_classes([AllowAny])
def register_view(request):
    username = (request.data.get("username") or "").strip()
    password = request.data.get("password") or ""
    if len(username) < 3 or len(password) < 8:
        return Response({"detail": "Username needs 3+ chars, password 8+ chars"}, status=400)
    if User.objects.filter(username=username).exists():
        return Response({"detail": "Username taken"}, status=409)
    user = User.objects.create_user(username=username, password=password)
    login(request, user)
    return Response({"username": user.username}, status=201)


@api_view(["POST"])
def logout_view(request):
    logout(request)
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([AllowAny])
def me_view(request):
    if not request.user.is_authenticated:
        return Response({"username": None})
    return Response({"username": request.user.username})


# --- datasets ---------------------------------------------------------------

class DatasetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DatasetSerializer
    parser_classes = [MultiPartParser, JSONParser]

    def get_queryset(self):
        return Dataset.objects.filter(owner=self.request.user)

    def create(self, request):
        """Upload. The web process only writes the file to disk; a worker opens it.

        Opening a 300 MB matrix inside the request handler is exactly the memory
        spike the worker tier exists to absorb.
        """
        s = DatasetUploadSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        f = s.validated_data["file"]
        ds = Dataset.objects.create(
            owner=request.user,
            name=s.validated_data.get("name") or f.name,
            file=f,
            original_filename=f.name,
            size_bytes=f.size,
        )
        enqueue(jobs.validate_dataset, str(ds.id))
        return Response(DatasetSerializer(ds).data, status=status.HTTP_201_CREATED)


# --- runs ---------------------------------------------------------------------

class RunViewSet(viewsets.ModelViewSet):
    serializer_class = RunSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = Run.objects.filter(owner=self.request.user)
        ds = self.request.query_params.get("dataset")
        return qs.filter(dataset_id=ds) if ds else qs

    def perform_create(self, serializer):
        run = serializer.save(owner=self.request.user, params=serializer.validated_data.get("params") or {})
        enqueue(jobs.run_pipeline, str(run.id))

    def create(self, request, *args, **kwargs):
        resp = super().create(request, *args, **kwargs)
        resp.status_code = status.HTTP_202_ACCEPTED       # accepted, not done: poll GET /runs/{id}/
        return resp

    @action(detail=True, methods=["get"])
    def cells(self, request, pk=None):
        """Column arrays, not row objects. Four parallel arrays are about 5x
        smaller than a list of dicts, and they are what a scatter plot wants."""
        run = self.get_object()
        if run.status not in (Run.Status.MARKERS, Run.Status.DONE):
            return Response({"detail": f"run is {run.status}"}, status=status.HTTP_409_CONFLICT)
        rows = list(run.cells.order_by("id").values_list("cell_barcode", "umap_x", "umap_y", "cluster_id"))
        barcodes, xs, ys, cl = zip(*rows) if rows else ((), (), (), ())
        return Response({"n": len(barcodes), "barcodes": barcodes, "x": xs, "y": ys, "cluster": cl})

    @action(detail=True, methods=["get"])
    def clusters(self, request, pk=None):
        run = self.get_object()
        sizes = run.cells.values("cluster_id").annotate(n=Count("id")).order_by("cluster_id")
        return Response([{"cluster_id": s["cluster_id"], "n_cells": s["n"]} for s in sizes])

    @action(detail=True, methods=["get"])
    def markers(self, request, pk=None):
        run = self.get_object()
        qs = MarkerGene.objects.filter(run=run)
        c = request.query_params.get("cluster")
        if c is not None:
            qs = qs.filter(cluster_id=int(c))
        return Response({"status": run.status, "markers": MarkerGeneSerializer(qs, many=True).data})
