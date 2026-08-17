"""
Central configuration for NHDPlus Vector Processing Units used by the
wastewater indicator pipeline.

All wastewater scripts should import VPU information from this module
instead of defining separate VPU configurations.
"""

from __future__ import annotations


VPU_CONFIG = {
    "01": {
        "region": "NE",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusNE/NHDPlusV21_NE_01_NHDSnapshotFGDB_04.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusNE/NHDPlusV21_NE_01_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusNE/NHDPlus01/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusNE/NHDPlus01/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "02": {
        "region": "MA",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDSnapshotFGDB_04.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMA/NHDPlus02/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMA/NHDPlus02/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "03N": {
        "region": "SA",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03N/NHDPlusV21_SA_03N_NHDSnapshotFGDB_04.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03N/NHDPlusV21_SA_03N_NHDPlusAttributes_07.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusSA/NHDPlus03N/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusSA/NHDPlus03N/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "03S": {
        "region": "SA",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03S/NHDPlusV21_SA_03S_NHDSnapshotFGDB_06.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03S/NHDPlusV21_SA_03S_NHDPlusAttributes_07.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusSA/NHDPlus03S/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusSA/NHDPlus03S/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "03W": {
        "region": "SA",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03W/NHDPlusV21_SA_03W_NHDSnapshotFGDB_04.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusSA/NHDPlus03W/NHDPlusV21_SA_03W_NHDPlusAttributes_07.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusSA/NHDPlus03W/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusSA/NHDPlus03W/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "04": {
        "region": "GL",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusGL/NHDPlusV21_GL_04_NHDSnapshotFGDB_08.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusGL/NHDPlusV21_GL_04_NHDPlusAttributes_14.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusGL/NHDPlus04/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusGL/NHDPlus04/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "05": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDSnapshotFGDB_06.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus05/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus05/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "06": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus06/NHDPlusV21_MS_06_NHDSnapshotFGDB_09.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus06/NHDPlusV21_MS_06_NHDPlusAttributes_10.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus06/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus06/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "07": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus07/NHDPlusV21_MS_07_NHDSnapshotFGDB_08.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus07/NHDPlusV21_MS_07_NHDPlusAttributes_10.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus07/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus07/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "08": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus08/NHDPlusV21_MS_08_NHDSnapshotFGDB_07.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus08/NHDPlusV21_MS_08_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus08/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus08/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "10U": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus10U/NHDPlusV21_MS_10U_NHDSnapshotFGDB_07.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus10U/NHDPlusV21_MS_10U_NHDPlusAttributes_10.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus10U/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus10U/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "10L": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus10L/NHDPlusV21_MS_10L_NHDSnapshotFGDB_06.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus10L/NHDPlusV21_MS_10L_NHDPlusAttributes_12.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus10L/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus10L/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "11": {
        "region": "MS",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus11/NHDPlusV21_MS_11_NHDSnapshotFGDB_06.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusMS/NHDPlus11/NHDPlusV21_MS_11_NHDPlusAttributes_08.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusMS/NHDPlus11/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusMS/NHDPlus11/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "09": {
        "region": "SR",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusSR/NHDPlusV21_SR_09_NHDSnapshotFGDB_07.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusSR/NHDPlusV21_SR_09_NHDPlusAttributes_07.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusSR/NHDPlus09/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusSR/NHDPlus09/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "12": {
        "region": "TX",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusTX/NHDPlusV21_TX_12_NHDSnapshotFGDB_05.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusTX/NHDPlusV21_TX_12_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusTX/NHDPlus12/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusTX/NHDPlus12/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "13": {
        "region": "RG",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusRG/NHDPlusV21_RG_13_NHDSnapshotFGDB_05.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusRG/NHDPlusV21_RG_13_NHDPlusAttributes_07.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusRG/NHDPlus13/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusRG/NHDPlus13/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "14": {
        "region": "CO",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusCO/NHDPlus14/NHDPlusV21_CO_14_NHDSnapshotFGDB_07.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusCO/NHDPlus14/NHDPlusV21_CO_14_NHDPlusAttributes_10.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusCO/NHDPlus14/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusCO/NHDPlus14/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "15": {
        "region": "CO",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusCO/NHDPlus15/NHDPlusV21_CO_15_NHDSnapshotFGDB_04.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusCO/NHDPlus15/NHDPlusV21_CO_15_NHDPlusAttributes_09.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusCO/NHDPlus15/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusCO/NHDPlus15/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "16": {
        "region": "GB",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusGB/NHDPlusV21_GB_16_NHDSnapshotFGDB_06.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusGB/NHDPlusV21_GB_16_NHDPlusAttributes_06.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusGB/NHDPlus16/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusGB/NHDPlus16/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "17": {
        "region": "PN",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusPN/NHDPlusV21_PN_17_NHDSnapshotFGDB_08.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusPN/NHDPlusV21_PN_17_NHDPlusAttributes_10.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusPN/NHDPlus17/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusPN/NHDPlus17/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
    "18": {
        "region": "CA",
        "snapshot_key": "NHDPlusV21/Data/NHDPlusCA/NHDPlusV21_CA_18_NHDSnapshotFGDB_05.7z",
        "attributes_key": "NHDPlusV21/Data/NHDPlusCA/NHDPlusV21_CA_18_NHDPlusAttributes_08.7z",
        "snapshot_relative_path": "snapshot_extracted/NHDPlusCA/NHDPlus18/NHDSnapshot/NHDSnapshot.gdb",
        "vaa_relative_path": "attributes_extracted/NHDPlusCA/NHDPlus18/NHDPlusAttributes/PlusFlowlineVAA.dbf",
    },
}


SUPPORTED_VPUS = tuple(sorted(VPU_CONFIG))
