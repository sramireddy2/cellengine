import numpy as np
from sklearn.metrics import adjusted_rand_score

from engine import cache
from engine.markers import rank_genes
from engine.params import ClusterParams, MarkerParams, PreprocessParams, params_from_dict
from engine.pipeline import cluster, preprocess

P = PreprocessParams(min_genes_per_cell=1, min_cells_per_gene=1, max_pct_mito=100,
                     n_top_genes=100, n_pcs=10, n_neighbors=10)


def test_cache_key_depends_only_on_dataset_and_params():
    a = PreprocessParams().cache_key("ds1")
    b = PreprocessParams().cache_key("ds1")
    c = PreprocessParams(n_pcs=30).cache_key("ds1")
    d = PreprocessParams().cache_key("ds2")
    assert a == b and a != c and a != d


def test_params_from_dict_routes_keys():
    pre, clu, mk = params_from_dict({"n_pcs": 20, "resolution": 0.5, "n_top": 10})
    assert pre.n_pcs == 20 and clu.resolution == 0.5 and mk.n_top == 10


def test_preprocess_recovers_planted_types(toy_adata):
    pre = preprocess(toy_adata, P)
    assert pre.pca.shape == (600, 10)
    assert pre.umap.shape == (600, 2)
    assert pre.connectivities.shape == (600, 600)
    labels = cluster(pre, ClusterParams(resolution=0.3))
    ari = adjusted_rand_score(toy_adata.obs["truth"], labels)
    assert ari > 0.95, f"ARI {ari}"
    assert labels.min() == 0 and np.bincount(labels).argmax() == 0  # cluster 0 is the largest


def test_resolution_changes_cluster_count(toy_adata):
    pre = preprocess(toy_adata, P)
    lo = cluster(pre, ClusterParams(resolution=0.1))
    hi = cluster(pre, ClusterParams(resolution=3.0))
    assert lo.max() < hi.max()


def test_cache_roundtrip(toy_adata):
    pre = preprocess(toy_adata, P)
    g, e = cache.serialize_graph(pre), cache.serialize_expr(pre)
    back = cache.deserialize(g, e, P)
    np.testing.assert_array_equal(back.pca, pre.pca)
    np.testing.assert_array_equal(back.umap, pre.umap)
    assert (back.connectivities != pre.connectivities).nnz == 0
    assert (back.lognorm != pre.lognorm).nnz == 0
    assert list(back.cell_barcodes) == list(pre.cell_barcodes)
    np.testing.assert_array_equal(cluster(back, ClusterParams()), cluster(pre, ClusterParams()))
    graph_only = cache.deserialize(g, None, P)
    assert graph_only.lognorm.nnz == 0 and graph_only.n_genes == pre.n_genes


def test_markers_find_planted_blocks(toy_adata):
    pre = preprocess(toy_adata, P)
    labels = cluster(pre, ClusterParams(resolution=0.3))
    df = rank_genes(pre.lognorm, pre.gene_names, labels, MarkerParams(n_top=30))
    truth = toy_adata.obs["truth"].to_numpy()
    for c in range(labels.max() + 1):
        t = np.bincount(truth[labels == c]).argmax()
        planted = {f"G{j}" for j in range(t * 30, (t + 1) * 30)}
        top = set(df[df.cluster == c].gene.head(30))
        assert len(top & planted) >= 25, f"cluster {c}: {len(top & planted)} of 30 planted genes"
