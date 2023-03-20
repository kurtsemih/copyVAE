#! /usr/bin/env python3

import numpy as np
import pandas as pd
import scanpy as sc
import pybiomart
from copyvae.preprocess import annotate_data


CHR_BASE_PAIRS = np.array([
    248956422,
    242193529,
    198295559,
    190214555,
    181538259,
    170805979,
    159345973,
    145138636,
    138394717,
    133797422,
    135086622,
    133275309,
    114364328,
    107043718,
    101991189,
    90338345,
    83257441,
    80373285,
    58617616,
    64444167,
    46709983,
    50818468,
    156040895])


def build_gene_map(chr_pos=CHR_BASE_PAIRS):
    """ Build gene map from meta data

    """
    server = pybiomart.Server(host='http://www.ensembl.org')
    mart = server['ENSEMBL_MART_ENSEMBL']
    dataset = mart['hsapiens_gene_ensembl']
    attributes = [#'ensembl_transcript_id',
                  'external_gene_name', 
                  'ensembl_gene_id',
                  'chromosome_name',
                  'start_position',
                  'end_position']
    
    gene_info = dataset.query(attributes= attributes)
    gene_info.rename(columns={'Chromosome/scaffold name': 'chr'}, inplace=True)

    gene_info.loc[gene_info.chr == 'X', 'chr'] = '23'
    gene_info = gene_info[
        gene_info['chr'].isin(
            np.arange(1, 24).astype(str)
        )
    ]
    gene_info = gene_info.copy()
    gene_info.loc[:, 'chr'] = gene_info.chr.astype(int)
    gene_info = gene_info.sort_values(by=['chr', 'Gene start (bp)'])

    #gene_map = gene_info[gene_info['Gene type']=='protein_coding'].copy()
    gene_map = gene_info
    gene_map['abspos'] = gene_map['Gene start (bp)']
    for i in range(len(chr_pos)):
        gene_map.loc[gene_map['chr'] == i + 1, 'abspos'] += chr_pos[:i].sum()

    return gene_map


def bin_genes_from_text(umi_counts, bin_size):
    """ Gene binning for UMI counts from text file

    Args:
        umi_counts: text file
        bin_size: number of genes per bin (int)

    Returns:
        data: (anndata object) high expressed gene counts,
                        number of genes fully divided by bin_size
        chrom_list: list of chromosome boundry bins
    """

    umis = pd.read_csv(umi_counts, sep='\t', low_memory=False).reset_index()
    labels = umis.iloc[1, :]

    gene_map = build_gene_map()

    # remove cell cycle genes
    umis = umis[~umis['index'].str.contains("HLA")]
    # gene name mapping
    sorted_gene = pd.merge(
        umis, gene_map, right_on=['Gene name'],
        left_on=['index'], how='inner'
    )
    sorted_gene = sorted_gene.sort_values(by=['abspos'])
    # remove non-expressed genes
    ch = sorted_gene.iloc[:, 1:-7].astype('int32')
    nonz = np.count_nonzero(ch, axis=1)
    sorted_gene['expressed'] = nonz
    expressed_gene = sorted_gene[sorted_gene['expressed'] > 0]

    # bin and remove exceeded genes
    n_exceeded = expressed_gene.chr.value_counts() % bin_size
    for chrom in n_exceeded.index:
        n = n_exceeded[chrom]
        ind = expressed_gene[
            expressed_gene['chr'] == chrom
        ].sort_values(by=['expressed', 'Gene start (bp)']
                      )[:n].index
        expressed_gene = expressed_gene.drop(index=ind)

    abs_pos = expressed_gene['abspos'].values
    with open('abs.npy', 'wb') as f:
        np.save(f, abs_pos)

    # extract chromosome boundary
    bin_number = expressed_gene.chr.value_counts() // bin_size
    chrom_bound = bin_number.sort_index().cumsum()
    chrom_list = [(0, chrom_bound[1])]
    for i in range(2, 24):
        start_p = chrom_bound[i - 1]
        end_p = chrom_bound[i]
        chrom_list.append((start_p, end_p))

    # clean and add labels
    expressed_gene.drop(columns=expressed_gene.columns[-7:],
                        axis=1, inplace=True)
    expressed_gene = pd.concat([expressed_gene.T, labels], axis=1)
    expressed_gene.rename(columns=expressed_gene.iloc[0], inplace=True)
    expressed_gene.drop(expressed_gene.index[0], inplace=True)
    #expressed_gene = expressed_gene.sort_values(by='cluster.pred')
    #expressed_gene.to_csv('bined_expressed_cell.csv', sep='\t')
    data = annotate_data(expressed_gene, abs_pos)

    return data, chrom_list


def bin_genes_from_anndata(file, bin_size):
    """ Gene binning for UMI counts for 10X data

    Args:
        file: h5 file
        bin_size: number of genes per bin (int)
        gene_metadata: gene name map

    Returns:
        data: (anndata object) high expressed gene counts,
                        number of genes fully divided by bin_size
        chrom_list: list of chromosome boundry bins
    """

    #adata = sc.read_h5ad(file)
    adata=file
    #adata.var.drop_duplicates(subset=['gene_ids'],inplace =True)
    #adata = sc.read(file)
    gene_map = build_gene_map()

    # normalize UMI counts
    sc.pp.filter_genes(adata, min_cells=1)
    #sc.pp.normalize_total(adata, inplace=True)
    #adata.X = np.round(adata.X)

    # extract genes
    """gene_df = pd.merge(
        adata.var,
        gene_map,
        right_on=['Gene name'],#['Gene stable ID'],
        left_on=['gene_ids'],
        how='right').dropna()
    gene_df = gene_df.drop_duplicates(subset=['gene_ids'])
    adata_clean = adata[:, adata.var.gene_ids.isin(gene_df.gene_ids.values)].copy()

    # add position in genome
    adata_clean.var['chr'] = gene_df['chr'].values
    adata_clean.var['abspos'] = gene_df['abspos'].values"""

    map_chr = dict(gene_map[['Gene name', 'chr']].values)
    adata.var['chr'] = adata.var.gene_ids.map(map_chr)
    map_abs = dict(gene_map[['Gene name', 'abspos']].values)
    adata.var['abspos'] = adata.var.gene_ids.map(map_abs)
    adata_clean = adata[:, (adata.var.chr.notna() & adata.var.abspos.notna())].copy()
    adata_clean.var['chr'] =  adata_clean.var.chr.astype('int')
    adata_clean.var['abspos'] =  adata_clean.var.abspos.astype('int')
    adata_clean = adata_clean[:, list(adata_clean.var.sort_values(by = 'abspos'.split()).index)].copy()

    # filter out low expressed genes
    #sc.pp.filter_genes(adata_clean, min_cells=100)
    # remove exceeded genes
    n_exceeded = adata_clean.var['chr'].value_counts() % bin_size
    ind_list = []
    for chrom in n_exceeded.index:
        n = n_exceeded[chrom]
        ind = adata_clean.var[adata_clean.var['chr'] == chrom].sort_values(
                                by=['n_cells', 'abspos'])[:n].index.values
        ind_list.append(ind)
    data = adata_clean[:, ~adata_clean.var.index.isin(
                            np.concatenate(ind_list))]
    #with open('abs.npy', 'wb') as f:
    #    np.save(f, data.var['abspos'].values)

    # find chromosome boundry bins
    bin_number = adata_clean.var['chr'].value_counts() // bin_size
    chrom_bound = bin_number.sort_index().cumsum()
    chrom_list = [(0, chrom_bound[1])]
    for i in range(2, 24):
        start_p = chrom_bound[i - 1]
        end_p = chrom_bound[i]
        chrom_list.append((start_p, end_p))

    return data, chrom_list
