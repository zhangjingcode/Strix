import os, sys, time, torch, random, tqdm, math
import numpy as np
from utils_cw import Print, load_h5, check_dir
import nibabel as nib

from typing import Any, Callable, Dict, Hashable, List, Mapping, Optional, Sequence, Tuple, Union
from medlp.data_io import CLASSIFICATION_DATASETS, SEGMENTATION_DATASETS
from medlp.data_io.base_dataset.segmentation_dataset import SupervisedSegmentationDataset3D, UnsupervisedSegmentationDataset3D
from medlp.data_io.base_dataset.classification_dataset import SupervisedClassificationDataset3D
from medlp.utilities.utils import is_avaible_size
from medlp.utilities.transforms import (LabelMorphologyD, 
                                        DataLabellingD, 
                                        RandMarginalCropByMaskD, 
                                        RandMarginalCrop2DByMaskD, 
                                        RandLabelToMaskD, 
                                        MaskIntensityExD
                                        )

import monai
from monai.config import IndexSelection, KeysCollection
from monai.data import CacheDataset, Dataset, PersistentDataset
from monai.utils import Method, NumpyPadMode, ensure_tuple, ensure_tuple_rep, fall_back_tuple, InterpolateMode
from monai.transforms.utils import generate_pos_neg_label_crop_centers
from monai.transforms import *


#win width: 1130.0, 19699.0
@SEGMENTATION_DATASETS.register('rjh_tswi', '3D', 
    "/homes/clwang/Data/RJH/RJ_data/preprocessed/labeled_data_list.json")
def get_rjh_tswi_seg_dataset(files_list, phase, opts):
    spacing=(0.667,0.667,1.34)
    winlevel=(1130.0, 19699.0)
    in_channels=opts.get('input_nc', 1)
    crop_size=opts.get('crop_size', (96,96,64))
    preload=opts.get('preload', 0)
    augment_ratio=opts.get('augment_ratio',0.4)
    orientation='RAI'
    cache_dir=check_dir(os.path.dirname(opts.get('experiment_path')),'caches')

    assert in_channels == 1, 'Currently only support single channel input'

    cropper = RandCropByPosNegLabeld(keys=["image","label"], label_key='label', pos=1, neg=1, spatial_size=crop_size) if \
              is_avaible_size(crop_size) else None
    
    if phase == 'train':
        additional_transforms = [
            #RandFlipD(keys=["image","label"], prob=augment_ratio, spatial_axis=[2]),
            RandRotateD(keys=["image","label"], range_x=math.pi/40, range_y=math.pi/40, range_z=math.pi/40, prob=augment_ratio, padding_mode='zeros'),
            Rand3DElasticD(keys=["image","label"], prob=augment_ratio, sigma_range=(5,10),
                           magnitude_range=(50,150), mode=["bilinear","nearest"], padding_mode='zeros')
        ]
    elif phase == 'valid':
        additional_transforms = []
    elif phase == 'test':
        cropper = None
        additional_transforms = []
    elif phase == 'test_wo_label':
        return UnsupervisedSegmentationDataset3D(
                    files_list,
                    orienter=Orientationd(keys='image', axcodes=orientation),
                    spacer=SpacingD(keys="image", pixdim=spacing),
                    resizer=None,
                    rescaler=NormalizeIntensityD(keys='image'),
                    cropper=None,
                    additional_transforms=[],
                    preload=0,
                    cache_dir=cache_dir,
                ).get_dataset()
    else:
        raise ValueError

    dataset = SupervisedSegmentationDataset3D(
        files_list,
        orienter=Orientationd(keys=['image','label'], axcodes=orientation),
        spacer=SpacingD(keys=["image","label"], pixdim=spacing, mode=[GridSampleMode.BILINEAR,GridSampleMode.NEAREST]),
        resizer=None,
        rescaler=[NormalizeIntensityD(keys='image'),]+[DataLabellingD(keys='label'),
                                                       LabelMorphologyD(keys='label',mode='dilation',radius=1,binary=False)],
        cropper=cropper,
        additional_transforms=additional_transforms,
        preload=preload,
        cache_dir=cache_dir,
    ).get_dataset()

    return dataset


@SEGMENTATION_DATASETS.register('rjh_swim', '3D', 
    "/homes/clwang/Data/RJH/RJ_data/SWIM_preprocessed/swim_train.json")
def get_rjh_swim_seg_dataset(files_list, phase, opts):
    spacing=(0.666667,0.666667,2)
    in_channels=opts.get('input_nc', 1)
    crop_size=opts.get('crop_size', (96,96,48))
    preload=opts.get('preload', 0)
    augment_ratio=opts.get('augment_ratio',0.4)
    orientation='RAI'
    cache_dir=check_dir(os.path.dirname(opts.get('experiment_path')),'caches')

    assert in_channels == 1, 'Currently only support single channel input'

    cropper = [RandCropByPosNegLabeld(keys=["image","label"], label_key='label', pos=1, neg=1, spatial_size=crop_size)] if is_avaible_size(crop_size) else []
    #cropper = [RandSpatialCropD(keys=['image','label'], roi_size=crop_size, random_size=False)] if is_avaible_size(crop_size) else []
    if phase == 'train':
        additional_transforms = [
            RandFlipD(keys=["image","label"], prob=augment_ratio, spatial_axis=[2]),
            RandRotateD(keys=["image","label"], range_x=math.pi/40, range_y=math.pi/40, range_z=math.pi/40, prob=augment_ratio, padding_mode='border'),
            Rand3DElasticD(keys=["image","label"], prob=augment_ratio, sigma_range=(5,10),
                           magnitude_range=(50,150), mode=["bilinear","nearest"], padding_mode='zeros')
        ]
    elif phase == 'valid':
        additional_transforms = []
    elif phase == 'test':
        cropper = None
        additional_transforms = []
    elif phase == 'test_wo_label':
        return UnsupervisedSegmentationDataset3D(
                    files_list,
                    orienter=Orientationd(keys='image', axcodes=orientation),
                    spacer=SpacingD(keys="image", pixdim=spacing),
                    resizer=None,
                    rescaler=ScaleIntensityRangePercentilesD(keys='image',lower=0.1, upper=99.9, b_min=0, b_max=1, clip=True),
                    cropper=None,
                    additional_transforms=[],
                    preload=0,
                    cache_dir=cache_dir,
                ).get_dataset()
    else:
        raise ValueError

    dataset = SupervisedSegmentationDataset3D(
        files_list,
        orienter=Orientationd(keys=['image','label'], axcodes=orientation),
        spacer=SpacingD(keys=["image","label"], pixdim=spacing),
        resizer=None,
        rescaler=ScaleIntensityRangePercentilesD(keys='image',lower=0.1, upper=99.9, b_min=0, b_max=1, clip=True),
        cropper=[CropForegroundD(keys=['image','label'],source_key='image',select_fn=lambda x: x > 0.45, margin=5),]+cropper,
        additional_transforms=additional_transforms,
        preload=preload,
        cache_dir=cache_dir,
    ).get_dataset()

    return dataset


@CLASSIFICATION_DATASETS.register('rjh_tswi', '3D', 
    "/homes/clwang/Data/RJH/STS_tSWI/datalist_wi_mask@1130_1537-train.json")
def get_rjh_tswi_cls_datasetV2(files_list, phase, opts):
    spacing=opts.get('spacing', (0.66667,0.66667,1.34))
    in_channels=opts.get('input_nc', 1)
    crop_size=opts.get('crop_size', (32,32,16))
    preload=opts.get('preload', 0)
    augment_ratio=opts.get('augment_ratio', 0.4)
    orientation=opts.get('orientation','RAI')
    cache_dir=check_dir(os.path.dirname(opts.get('experiment_path')),'caches')

    assert in_channels == 1, 'Currently only support single channel input'

    cropper = [
        RandLabelToMaskD(keys="mask", select_labels=[1,2], cls_label_key='label'),
        CropForegroundD(keys=["image","mask"], source_key="mask", margin=(5,5,2)),
        ResizeWithPadOrCropD(keys=["image","mask"], spatial_size=crop_size),
        Rand3DElasticD(keys="mask", prob=augment_ratio, sigma_range=(6,8), 
                       magnitude_range=(50,80), mode="nearest", padding_mode='zeros'),
        MaskIntensityExD(keys="image", mask_key="mask"),
        ]

    if phase == 'train':
        additional_transforms = [
            #RandFlipD(keys=["image","mask"], prob=augment_ratio, spatial_axis=[2]),
            RandRotate90D(keys=["image","mask"], prob=augment_ratio, max_k=3),
        ]
    elif phase == 'valid':
        additional_transforms = []
    elif phase == 'test':
        additional_transforms = []
        cropper = [
            RandLabelToMaskD(keys="mask", select_labels=[1,2], cls_label_key='label', select_msk_label=2), 
            CropForegroundD(keys=["image","mask"], source_key="mask", margin=(5,5,2)),
            ResizeWithPadOrCropD(keys=["image","mask"], spatial_size=crop_size),
            MaskIntensityExD(keys="image", mask_key="mask"),
        ]
    elif phase == 'test_wo_label':
        raise NotImplementedError


    dataset = SupervisedClassificationDataset3D(
        files_list,
        loader = LoadNiftid(keys=["image","mask"], dtype=np.float32),
        channeler = AddChanneld(keys=["image","mask"]),
        orienter=Orientationd(keys=['image','mask'], axcodes=orientation),
        spacer=SpacingD(keys=["image","mask"], pixdim=spacing, mode=[GridSampleMode.BILINEAR,GridSampleMode.NEAREST]),
        resizer=None,
        rescaler=[
            NormalizeIntensityD(keys='image'),
            #ScaleIntensityRangePercentilesD(keys='image',lower=0.1, upper=99.9, b_min=0, b_max=1, clip=True),
            LabelMorphologyD(keys='mask',mode='closing', radius=2,binary=False),
            LabelMorphologyD(keys='mask',mode='dilation',radius=1,binary=False),
            ],
        cropper=cropper,
        additional_transforms=additional_transforms,
        caster=CastToTyped(keys="image", dtype=np.float32),
        to_tensor=ToTensord(keys=["image"]),
        preload=preload,
        cache_dir=cache_dir,
    ).get_dataset()

    return dataset


@CLASSIFICATION_DATASETS.register('rjh_tswi', '2D', 
    "/homes/clwang/Data/RJH/STS_tSWI/datalist_wi_mask@1130_1537-train.json")
def get_rjh_tswi_cls_dataset2D(files_list, phase, opts):
    spacing=opts.get('spacing', (0.667,0.667,1.34))
    in_channels=opts.get('input_nc', 1)
    image_size=opts.get('image_size', (96,96))
    crop_size=opts.get('crop_size', (48,48))
    preload=opts.get('preload', 0)
    augment_ratio=opts.get('augment_ratio', 0.4)
    orientation=opts.get('orientation', 'RAI')
    cache_dir=check_dir(opts.get('experiment_path'),'caches')

    assert in_channels in [1,3], 'Currently only support 1&3 channel input'

    neighbor_slice = (in_channels-1)//2
    cropper = [RandMarginalCrop2DByMaskD(keys='image',mask_key='mask',label_key='label',crop_size=crop_size,neighbor_slices=neighbor_slice,keep_largest=True),
               SqueezeDimD(keys=['image','mask']), 
               AsChannelFirstD(keys=['image','mask'])]
    if is_avaible_size(image_size):
        cropper += [ResizeWithPadOrCropD(keys=['image','mask'],spatial_size=image_size)]

    if phase == 'train':
        additional_transforms = [
            RandFlipD(keys=["image","mask"], prob=augment_ratio, spatial_axis=0),
            RandRotateD(keys=["image","mask"], range_x=math.pi/40, range_y=math.pi/40, prob=augment_ratio, padding_mode='reflection'),
        ]
    elif phase == 'valid':
        additional_transforms = []
    elif phase == 'test':
        additional_transforms = []
        cropper = [RandMarginalCrop2DByMaskD(keys='image', mask_key='mask',label_key='label',crop_size=crop_size,neighbor_slices=neighbor_slice,keep_largest=True,select_msk=1),
                   SqueezeDimD(keys=['image','mask']), 
                   AsChannelFirstD(keys=['image','mask'])]
        if is_avaible_size(image_size):
            cropper += [ResizeWithPadOrCropD(keys=['image','mask'],spatial_size=image_size)]
    elif phase == 'test_wo_label':
        raise NotImplementedError

    dataset = SupervisedClassificationDataset3D(
        files_list,
        loader = LoadNiftid(keys=["image","mask"], dtype=np.float32),
        channeler = [AddChanneld(keys="image"), AsChannelFirstD(keys="mask")],
        orienter=Orientationd(keys=['image','mask'], axcodes=orientation),
        spacer=SpacingD(keys=["image","mask"], pixdim=spacing, mode=[GridSampleMode.BILINEAR,GridSampleMode.NEAREST]),
        resizer=None,
        rescaler=NormalizeIntensityD(keys='image'),
        cropper=cropper, 
        additional_transforms=additional_transforms,
        caster=CastToTyped(keys=["image","mask"], dtype=[np.float32,np.int16]),
        to_tensor=ToTensord(keys=["image"]),
        preload=preload,
        cache_dir=cache_dir,
    ).get_dataset()

    return dataset
