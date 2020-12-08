from typing import Any, Callable, Dict, Hashable, List, Mapping, Optional, Sequence, Tuple, Union

import random
import torch
import numpy as np

from monai.config import IndexSelection, KeysCollection
from monai.transforms.compose import Transform, MapTransform, Randomizable
from monai.utils import ensure_tuple_rep, ensure_tuple, fall_back_tuple
from monai.transforms.utils import map_binary_to_indices, generate_pos_neg_label_crop_centers
from monai.transforms import SpatialCrop, MaskIntensity

from medlp.models.rcnn.structures.bounding_box import BoxList
from medlp.utilities.utils import is_avaible_size, bbox_2D, bbox_3D
from utils_cw import get_connected_comp
from scipy import ndimage as ndi
from skimage import exposure

class CoordToBoxList(Transform):
    """
    Converts the input data to a BoxList without applying any other transformations.
    """

    def __call__(self, 
                 coord: Union[np.ndarray, list, tuple],
                 label: int, 
                 shape: Union[np.ndarray, list, tuple],
                 box_radius: Union[np.ndarray, list, tuple]) -> BoxList:
        
        boxes = [[coord[0]-box_radius[0], coord[1]-box_radius[1], 
                  coord[0]+box_radius[0], coord[1]+box_radius[1]]]
        boxes = torch.as_tensor(boxes).reshape(-1, 4)
        # and labels
        labels = torch.tensor([label])

        # create a BoxList from the boxes
        boxlist = BoxList(boxes, shape, mode="xyxy")
        # add the labels to the boxlist
        boxlist.add_field("labels", labels)

        return boxlist


class CoordToBoxListd(MapTransform):
    """
    Dictionary-based wrapper of transform CoordToBoxList.
    """

    def __init__(self, 
                 keys: KeysCollection,
                 box_radius:  Union[np.ndarray, list, tuple], 
                 image_shape: Union[np.ndarray, list, tuple],
                 label_key: Optional[KeysCollection] = None,
                 
        ) -> None:
        """
        Args:
            keys: keys of the corresponding items to be transformed.
                See also: :py:class:`monai.transforms.compose.MapTransform`
        """
        super().__init__(keys)
        self.radius = box_radius
        self.image_shape = image_shape
        self.label_key = label_key
        self.converter = CoordToBoxList()

    def __call__(self, data: Mapping[Hashable, Union[np.ndarray, torch.Tensor]]) -> Dict[Hashable, torch.Tensor]:
        d = dict(data)
        label_data = 1 if self.label_key is None else d[self.label_key]
        
        for key in self.keys:
            d[key] = self.converter(d[key], label=label_data, shape=self.image_shape, box_radius=self.radius)
        return d


class LabelMorphology(Transform):
    def __init__(self, 
                 mode: str,
                 radius: int,
                 binary: bool):
        """
        Args:
            mode: morphology mode, e.g. 'closing', 'dilation', 'erosion', 'opening'
            radius: radius of morphology operation.
            binary: whether using binary morphology (for binary data)

        """
        self.mode = mode
        self.radius = radius
        self.binary = binary
        assert self.mode in ['closing', 'dilation', 'erosion', 'opening'], \
            f"Mode must be one of 'closing', 'dilation', 'erosion', 'opening', but got {self.mode}"

    def __call__(self, 
                 img: np.ndarray, 
                 mode: Optional[str]=None,
                 radius: Optional[int]=None,
                 binary: Optional[bool]=None) -> np.ndarray:
        """
        Apply the transform to `img`.

        """
        self.mode = self.mode if mode is None else mode
        self.radius = self.radius if radius is None else radius
        self.binary = self.binary if binary is None else binary

        input_ndim = img.squeeze().ndim # spatial ndim
        if input_ndim == 2:
            structure = ndi.generate_binary_structure(2, 1)
        elif input_ndim == 3:
            structure = ndi.generate_binary_structure(3, 1)
        else:
            raise ValueError('Currently only support 2D&3D data')
        
        channel_dim = None
        if input_ndim != img.ndim:
            channel_dim = img.shape.index(1)
            img = img.squeeze()

        if self.mode == 'closing':
            if self.binary:
                img = ndi.binary_closing(img, structure=structure, iterations=self.radius)
            else:
                for _ in range(self.radius):
                    img = ndi.grey_closing(img, footprint=structure)        
        elif self.mode == 'dilation':
            if self.binary:
                img = ndi.binary_dilation(img, structure=structure, iterations=self.radius)
            else:
                for _ in range(self.radius):
                    img = ndi.grey_dilation(img, footprint=structure)
        elif self.mode == 'erosion':
            if self.binary:
                img = ndi.binary_erosion(img, structure=structure, iterations=self.radius)
            else:
                for _ in range(self.radius):
                    img = ndi.grey_erosion(img, footprint=structure)
        elif self.mode == 'opening':
            if self.binary:
                img = ndi.binary_opening(img, structure=structure, iterations=self.radius)
            else:
                for _ in range(self.radius):
                    img = ndi.grey_opening(img, footprint=structure)
        else:
            raise ValueError(f'Unexpected keyword {self.mode}')
        
        if channel_dim is not None:
            return np.expand_dims(img, axis=channel_dim)
        else:
            return img

class LabelMorphologyD(MapTransform):
    """
    Dictionary-based wrapper of :py:class:`DataMorphology`.
    """

    def __init__(
        self,
        keys: KeysCollection,
        mode: str,
        radius: int,
        binary: bool,
    ) -> None:
        super().__init__(keys)
        self.mode = ensure_tuple_rep(mode, len(self.keys))
        self.radius = ensure_tuple_rep(radius, len(self.keys))
        self.binary = ensure_tuple_rep(binary, len(self.keys))
        self.converter = LabelMorphology('dilation', 0, True)

    def __call__(self, data: Mapping[Hashable, np.ndarray]) -> Dict[Hashable, np.ndarray]:
        d = dict(data)
        for idx, key in enumerate(self.keys):
            if self.radius[idx] <= 0:
                continue
            d[key] = self.converter(d[key], mode=self.mode[idx], radius=self.radius[idx], binary=self.binary[idx])
        return d


class DataLabelling(Transform):
    def __init__(self) -> None:
        """
        Args:
            to_onehot: whether convert labelling data to onehot format.

        """
        #self.to_onehot = to_onehot
    
    def __call__(self, img: np.ndarray) -> np.ndarray:
        input_ndim = img.squeeze().ndim # spatial ndim
        if input_ndim == 2:
            structure = ndi.generate_binary_structure(2, 1)
        elif input_ndim == 3:
            structure = ndi.generate_binary_structure(3, 1)
        else:
            raise ValueError('Currently only support 2D&3D data')
        
        channel_dim = None
        if input_ndim != img.ndim:
            channel_dim = img.shape.index(1)
            img = img.squeeze()

        ccs, num_features = ndi.label(img, structure=structure)
        
        if channel_dim is not None:
            return np.expand_dims(ccs, axis=channel_dim)

        return ccs

class DataLabellingD(MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
    ) -> None:
        super().__init__(keys)
        self.converter = DataLabelling()

    def __call__(self, img: Mapping[Hashable, torch.Tensor]) -> Dict[Hashable, torch.Tensor]:
        d = dict(img)
        for idx, key in enumerate(self.keys):
            d[key] = self.converter(d[key])
        return d


class Clahe(Transform):
    def __init__(self, kernel_size=None, clip_limit=0.01, nbins=256) -> None:
        self.kernel_size=kernel_size 
        self.clip_limit=clip_limit
        self.nbins=nbins

    def __call__(self, img: np.ndarray) -> np.ndarray:
        input_ndim = img.squeeze().ndim # spatial ndim
        assert input_ndim in [2, 3], 'Currently only support 2D&3D data'

        channel_dim = None
        if input_ndim != img.ndim:
            channel_dim = img.shape.index(1)
            img = img.squeeze()
        
        filter_img = exposure.equalize_adapthist(img, kernel_size=self.kernel_size, clip_limit=self.clip_limit, nbins=self.nbins)

        if channel_dim is not None:
            return np.expand_dims(filter_img, axis=channel_dim)
        else:
            return filter_img
        
class ClaheD(MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
        kernel_size=None, 
        clip_limit=0.01, 
        nbins=256
    ) -> None:
        super().__init__(keys)
        self.converter = Clahe()
        self.kernel_size = kernel_size
        self.clip_limit = clip_limit
        self.nbins = nbins

    def __call__(self, img: Mapping[Hashable, torch.Tensor]) -> Dict[Hashable, torch.Tensor]:
        d = dict(img)
        for idx, key in enumerate(self.keys):
            d[key] = self.converter(d[key])
        return d


class RandLabelToMask(Randomizable, Transform):
    """
    Convert labels to mask for other tasks. A typical usage is to convert segmentation labels
    to mask data to pre-process images and then feed the images into classification network.
    It can support single channel labels or One-Hot labels with specified `select_labels`.
    For example, users can select `label value = [2, 3]` to construct mask data, or select the
    second and the third channels of labels to construct mask data.
    The output mask data can be a multiple channels binary data or a single channel binary
    data that merges all the channels.

    Args:
        select_labels: labels to generate mask from. for 1 channel label, the `select_labels`
            is the expected label values, like: [1, 2, 3]. for One-Hot format label, the
            `select_labels` is the expected channel indices.
        merge_channels: whether to use `np.any()` to merge the result on channel dim. if yes,
            will return a single channel mask with binary data.

    """

    def __init__(  # pytype: disable=annotation-type-mismatch
        self,
        select_labels: Union[Sequence[int], int],
        merge_channels: bool = False,
    ) -> None:  # pytype: disable=annotation-type-mismatch
        self.select_labels = ensure_tuple(select_labels)
        self.merge_channels = merge_channels

    def randomize(self):
        self.select_label = self.R.choice(self.select_labels, 1)[0]

    def __call__(
        self, img: np.ndarray, select_label: Optional[Union[Sequence[int], int]] = None, merge_channels: bool = False
    ) -> np.ndarray:
        """
        Args:
            select_labels: labels to generate mask from. for 1 channel label, the `select_labels`
                is the expected label values, like: [1, 2, 3]. for One-Hot format label, the
                `select_labels` is the expected channel indices.
            merge_channels: whether to use `np.any()` to merge the result on channel dim. if yes,
                will return a single channel mask with binary data.
        """
        if select_label is None:         
            self.randomize()
        else:
            self.select_label = select_label

        if img.shape[0] > 1:
            data = img[[self.select_label]]
        else:
            data = np.where(np.in1d(img, self.select_label), True, False).reshape(img.shape)

        return np.any(data, axis=0, keepdims=True) if (merge_channels or self.merge_channels) else data

class RandLabelToMaskD(Randomizable, MapTransform):
    """
    Dictionary-based wrapper of :py:class:`RandLabelToMask`.

    Args:
        keys: keys of the corresponding items to be transformed.
            See also: :py:class:`monai.transforms.compose.MapTransform`
        select_labels: labels to generate mask from. for 1 channel label, the `select_labels`
            is the expected label values, like: [1, 2, 3]. for One-Hot format label, the
            `select_labels` is the expected channel indices.
        merge_channels: whether to use `np.any()` to merge the result on channel dim.
            if yes, will return a single channel mask with binary data.

    """

    def __init__(  # pytype: disable=annotation-type-mismatch
        self,
        keys: KeysCollection,
        select_labels: Union[Sequence[int], int],
        merge_channels: bool = False,
        cls_label_key: Optional[KeysCollection] = None,
        select_msk_label: Optional[int] = None, #! for tmp debug
    ) -> None:
        super().__init__(keys)
        self.select_labels = select_labels
        self.cls_label_key = cls_label_key
        self.select_label = select_msk_label
        self.converter = RandLabelToMask(select_labels=select_labels, merge_channels=merge_channels)

    def randomize(self):
        self.select_label = self.R.choice(self.select_labels, 1)[0]

    def __call__(self, data: Mapping[Hashable, np.ndarray]) -> Dict[Hashable, np.ndarray]:
        d = dict(data)
        if self.select_label is None:
            self.randomize()

        if self.cls_label_key is not None:
            label = d[self.cls_label_key]
            assert len(label) == len(self.select_labels), 'length of cls_label_key must equal to length of mask select_labels'

            if isinstance(label, (list, tuple)):
                label = { i:L for i, L in enumerate(label, 1)}
            elif isinstance(label, (int, float)):
                label = {1:label}
            assert isinstance(label, dict), 'Only support dict type label'
            
            d[self.cls_label_key] = label[self.select_label]

        for key in self.keys:
            d[key] = self.converter(d[key], select_label=self.select_label)

        return d

class MaskIntensityExD(MapTransform):
    """
    Dictionary-based wrapper of :py:class:`monai.transforms.MaskIntensity`.

    Args:
        keys: keys of the corresponding items to be transformed.
            See also: :py:class:`monai.transforms.compose.MapTransform`
        mask_data: if mask data is single channel, apply to evey channel
            of input image. if multiple channels, the channel number must
            match input data. mask_data will be converted to `bool` values
            by `mask_data > 0` before applying transform to input image.

    """

    def __init__(self, keys: KeysCollection, mask_key: KeysCollection) -> None:
        super().__init__(keys)
        self.mask_key = mask_key
        self.converter = MaskIntensity(mask_data=None)

    def __call__(self, data: Mapping[Hashable, np.ndarray]) -> Dict[Hashable, np.ndarray]:
        d = dict(data)
        mask_data = d[self.mask_key]
        for key in self.keys:
            d[key] = self.converter(d[key], mask_data=mask_data)
        return d

class RandMarginalCropByMaskD(Randomizable, MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
        mask_key: KeysCollection,
        label_key: KeysCollection,
        margin_size: Union[Sequence[int], int] = 0,
        divide_by_k: int = 16,
        keep_largest: bool = True,
    ) -> None:
        super().__init__(keys)
        self.mask_key = mask_key
        self.label_key = label_key
        self.margin_size = margin_size
        self.keep_largest = keep_largest
        self.k = divide_by_k

    def randomize(
            self,
            label: list
        ) -> None:
        self.select_label = self.R.choice(ensure_tuple(label), 1)[0]

    def divisible_size(self, spatial_shape):
        k = fall_back_tuple(self.k, (1,) * len(spatial_shape))
        new_size = []
        for k_d, dim in zip(k, spatial_shape):
            new_dim = int(np.ceil(dim / k_d) * k_d) if k_d > 0 else dim
            new_size.append(new_dim)
        return new_size

    def __call__(self, img: Mapping[Hashable, torch.Tensor]) -> Dict[Hashable, torch.Tensor]:
        d = dict(img)
        
        for idx, key in enumerate(self.keys):
            data, mask, label = d[key], d[self.mask_key], d[self.label_key]
            if isinstance(label, dict):
                pass
            elif isinstance(label, (list, tuple)):
                label = { i:L for i, L in enumerate(label, 1)}
            elif isinstance(label, (int, float)):
                label = {1:label}
            
            assert data.shape == mask.shape, f'Data shape must equal to Mask shape, but got {data.shape} and {mask.shape}'
            assert len(np.unique(mask))-1 == len(label), f'Mask data unique num must equal to label num, but got {np.unique(mask)} and {label}'

            self.randomize(np.unique(mask[mask>0]).astype(np.int32))

            # handle one-hot
            if mask.shape[0] > 1:
                new_mask = mask[[self.select_label]]
            else:
                new_mask = np.where(np.in1d(mask, self.select_label), True, False).reshape(mask.shape)

            input_ndim = new_mask.squeeze().ndim # spatial ndim
            assert input_ndim in [2, 3], 'Currently only support 2D&3D data'
            self.margin_size = ensure_tuple_rep(self.margin_size, input_ndim)

            channel_dim = None
            if input_ndim != new_mask.ndim:
                channel_dim = new_mask.shape.index(1)
                new_mask = new_mask.squeeze() 
                new_data = data.squeeze()
            
            if self.keep_largest:
                new_mask = get_connected_comp(new_mask, topK=1, binary_output=True, min_th=0)

            if input_ndim == 2:
                rmin, rmax, cmin, cmax = bbox_2D(new_mask)
                rmin, rmax, cmin, cmax = rmin-self.margin_size[0], rmax+self.margin_size[0], cmin-self.margin_size[1], cmax+self.margin_size[1]
                crop_size = self.divisible_size((rmax-rmin,cmax-cmin))
                center_ = [(rmin+rmax)//2, (cmin+cmax)//2]
                d[key] = new_data[int(center_[0]-crop_size[0]/2):int(center_[0]+crop_size[0]/2), 
                                  int(center_[1]-crop_size[1]/2):int(center_[1]+crop_size[1]/2)]
                d[self.mask_key] = new_mask[int(center_[0]-crop_size[0]/2):int(center_[0]+crop_size[0]/2), 
                                            int(center_[1]-crop_size[1]/2):int(center_[1]+crop_size[1]/2)]

            elif input_ndim == 3:
                rmin, rmax, cmin, cmax, zmin, zmax = bbox_3D(new_mask)
                rmin, rmax, cmin, cmax, zmin, zmax = rmin-self.margin_size[0], rmax+self.margin_size[0], \
                                                     cmin-self.margin_size[1], cmax+self.margin_size[1], \
                                                     zmin-self.margin_size[2], zmax+self.margin_size[2]
                crop_size = self.divisible_size((rmax-rmin,cmax-cmin,zmax-zmin))
                center_ = [(rmin+rmax)//2, (cmin+cmax)//2, (zmin+zmax)//2]
                #print('old_crop_size', (rmax-rmin,cmax-cmin,zmax-zmin), 'crop_size:', crop_size, 'center:', center_)
                d[key] = new_data[int(center_[0]-crop_size[0]/2):int(center_[0]+crop_size[0]/2), 
                                  int(center_[1]-crop_size[1]/2):int(center_[1]+crop_size[1]/2),
                                  int(center_[2]-crop_size[2]/2):int(center_[2]+crop_size[2]/2)]
                d[self.mask_key] = new_mask[int(center_[0]-crop_size[0]/2):int(center_[0]+crop_size[0]/2), 
                                            int(center_[1]-crop_size[1]/2):int(center_[1]+crop_size[1]/2),
                                            int(center_[2]-crop_size[2]/2):int(center_[2]+crop_size[2]/2)]
            
            if channel_dim is not None:
                d[key] = np.expand_dims(d[key], axis=channel_dim)
                d[self.mask_key] = np.expand_dims(d[self.mask_key], axis=channel_dim)
            d[self.label_key] = label[self.select_label]
        return d


class RandMarginalCrop2DByMaskD(Randomizable, MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
        mask_key: KeysCollection,
        label_key: KeysCollection,
        crop_size: Union[Sequence[int], int] = 0,
        divide_by_k: int = 2,
        neighbor_slices: int = 0,
        keep_largest: bool = True,
        select_msk = None
    ) -> None:
        super().__init__(keys)
        self.mask_key = mask_key
        self.label_key = label_key
        self.crop_size = crop_size
        self.k = divide_by_k
        self.neighbor_slices = neighbor_slices
        self.keep_largest = keep_largest
        self.select_msk = select_msk #! tmp test

    def randomize(
            self,
            label: list
        ) -> None:
        self.select_msk = self.R.choice(ensure_tuple(label), 1)[0]

    def divisible_size(self, spatial_shape):
        k = fall_back_tuple(self.k, (1,) * len(spatial_shape))
        new_size = []
        for k_d, dim in zip(k, spatial_shape):
            new_dim = int(np.ceil(dim / k_d) * k_d) if k_d > 0 else dim
            new_size.append(new_dim)
        return new_size

    def __call__(self, img: Mapping[Hashable, torch.Tensor]) -> Dict[Hashable, torch.Tensor]:
        d = dict(img)
        
        for idx, key in enumerate(self.keys):
            data, mask, label = d[key], d[self.mask_key], d[self.label_key]
            if isinstance(label, dict):
                pass
            elif isinstance(label, (list, tuple)):
                label = { i:L for i, L in enumerate(label, 1)}
            elif isinstance(label, (int, float)):
                label = {1:label}
            
            assert data.shape == mask.shape, f'Data shape must equal to Mask shape, but got {data.shape} and {mask.shape}'
            assert len(np.unique(mask))-1 == len(label), f'Mask data unique num must equal to label num, but got {np.unique(mask)} and {label}'

            if self.select_msk is None:
                self.randomize(np.unique(mask[mask>0]).astype(np.int32))

            # handle one-hot
            if mask.shape[0] > 1:
                new_mask = mask[[self.select_msk]]
            else:
                new_mask = np.where(np.in1d(mask, self.select_msk), True, False).reshape(mask.shape)

            input_ndim = new_mask.squeeze().ndim # spatial ndim
            assert input_ndim in [2, 3], 'Currently only support 2D&3D data'
            self.crop_size = ensure_tuple_rep(self.crop_size, 2)

            channel_dim = None
            if input_ndim != new_mask.ndim:
                channel_dim = new_mask.shape.index(1)
                new_mask = new_mask.squeeze() 
                new_data = data.squeeze()
            
            if self.keep_largest:
                new_mask = get_connected_comp(new_mask, topK=1, binary_output=True, min_th=0)

            if input_ndim == 2:
                rmin, rmax, cmin, cmax = bbox_2D(new_mask)
                center_ = [(rmin+rmax)//2, (cmin+cmax)//2]
                rmin, rmax, cmin, cmax = center_[0]-self.crop_size[0]//2, center_[0]+self.crop_size[0]//2, center_[1]-self.crop_size[1]//2, center_[1]+self.crop_size[1]//2
                spatial_size = self.divisible_size((rmax-rmin,cmax-cmin)) if self.k > 0 else (rmax-rmin, cmax-cmin)
                d[key] = new_data[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                  int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2)]
                d[self.mask_key] = new_mask[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                            int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2)]

            elif input_ndim == 3:
                rmin, rmax, cmin, cmax, zmin, zmax = bbox_3D(new_mask)
                z_ = random.choice(range(zmin+(zmax-zmin)//3, zmax-(zmax-zmin)//3))
                center_ = [(rmin+rmax)//2, (cmin+cmax)//2]
                rmin, rmax, cmin, cmax, zmin, zmax = center_[0]-self.crop_size[0]//2, center_[0]+self.crop_size[0]//2, \
                                                     center_[1]-self.crop_size[1]//2, center_[1]+self.crop_size[1]//2, \
                                                     z_-self.neighbor_slices, z_+self.neighbor_slices+1
                spatial_size = self.divisible_size((rmax-rmin,cmax-cmin)) if self.k > 0 else (rmax-rmin, cmax-cmin)
                
                d[key] = new_data[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                  int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2),
                                  int(zmin):int(zmax)]
                d[self.mask_key] = new_mask[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                            int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2),
                                            int(zmin):int(zmax)]
            
            if channel_dim is not None:
                d[key] = np.expand_dims(d[key], axis=channel_dim)
                d[self.mask_key] = np.expand_dims(d[self.mask_key], axis=channel_dim)
            d[self.label_key] = label[self.select_msk]
        return d


class MarginalCrop2DByMaskD(MapTransform):
    def __init__(
        self,
        keys: KeysCollection,
        mask_key: KeysCollection,
        label_key: KeysCollection,
        crop_size: Union[Sequence[int], int] = 0,
        divide_by_k: int = 2,
        neighbor_slices: int = 0,
        keep_largest: bool = True,
    ) -> None:
        raise NotImplementedError('Not correct!')
        super().__init__(keys)
        self.mask_key = mask_key
        self.label_key = label_key
        self.crop_size = crop_size
        self.k = divide_by_k
        self.neighbor_slices = neighbor_slices
        self.keep_largest = keep_largest

    def divisible_size(self, spatial_shape):
        k = fall_back_tuple(self.k, (1,) * len(spatial_shape))
        new_size = []
        for k_d, dim in zip(k, spatial_shape):
            new_dim = int(np.ceil(dim / k_d) * k_d) if k_d > 0 else dim
            new_size.append(new_dim)
        return new_size

    def __call__(self, img: Mapping[Hashable, torch.Tensor]) -> Dict[Hashable, torch.Tensor]:
        d = dict(img)
        
        for idx, key in enumerate(self.keys):
            data, mask, label = d[key], d[self.mask_key], d[self.label_key]
            if isinstance(label, dict):
                pass
            elif isinstance(label, (list, tuple)):
                label = { i:L for i, L in enumerate(label, 1)}
            elif isinstance(label, (int, float)):
                label = {1:label}
            
            assert data.shape == mask.shape, f'Data shape must equal to Mask shape, but got {data.shape} and {mask.shape}'
            assert len(np.unique(mask))-1 == len(label), f'Mask data unique num must equal to label num, but got {np.unique(mask)} and {label}'

            labels = np.unique(mask[mask>0]).astype(np.int32)
            cropped_data, cropped_mask, select_labels = [], [], []
            for select_msk in labels:
                # handle one-hot
                if mask.shape[0] > 1:
                    new_mask = mask[[select_msk]]
                else:
                    new_mask = np.where(np.in1d(mask, select_msk), True, False).reshape(mask.shape)

                select_labels.append(label[select_msk])
                input_ndim = new_mask.squeeze().ndim # spatial ndim
                assert input_ndim in [2, 3], 'Currently only support 2D&3D data'
                self.crop_size = ensure_tuple_rep(self.crop_size, 2)

                channel_dim = None
                if input_ndim != new_mask.ndim:
                    channel_dim = new_mask.shape.index(1)
                    new_mask = new_mask.squeeze() 
                    new_data = data.squeeze()
                
                if self.keep_largest:
                    new_mask = get_connected_comp(new_mask, topK=1, binary_output=True, min_th=0)

                if input_ndim == 2:
                    rmin, rmax, cmin, cmax = bbox_2D(new_mask)
                    center_ = [(rmin+rmax)//2, (cmin+cmax)//2]
                    rmin, rmax, cmin, cmax = center_[0]-self.crop_size[0]//2, center_[0]+self.crop_size[0]//2, center_[1]-self.crop_size[1]//2, center_[1]+self.crop_size[1]//2
                    spatial_size = self.divisible_size((rmax-rmin,cmax-cmin)) if self.k > 0 else (rmax-rmin, cmax-cmin)
                    crop_data = new_data[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                         int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2)]
                    crop_mask = new_mask[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                          int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2)]
                    if channel_dim is not None:
                        crop_data = np.expand_dims(crop_data, axis=channel_dim)
                        crop_mask = np.expand_dims(crop_mask, axis=channel_dim)
                    cropped_data.append(crop_data)
                    cropped_mask.append(crop_mask)

                elif input_ndim == 3:
                    rmin, rmax, cmin, cmax, zmin, zmax = bbox_3D(new_mask)
                    z_ = random.choice(range(zmin+(zmax-zmin)//3, zmax-(zmax-zmin)//3))
                    center_ = [(rmin+rmax)//2, (cmin+cmax)//2]
                    rmin, rmax, cmin, cmax, zmin, zmax = center_[0]-self.crop_size[0]//2, center_[0]+self.crop_size[0]//2, \
                                                        center_[1]-self.crop_size[1]//2, center_[1]+self.crop_size[1]//2, \
                                                        z_-self.neighbor_slices, z_+self.neighbor_slices+1
                    spatial_size = self.divisible_size((rmax-rmin,cmax-cmin)) if self.k > 0 else (rmax-rmin, cmax-cmin)
                    
                    crop_data = new_data[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                         int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2),
                                         int(zmin):int(zmax)]
                    crop_mask = new_mask[int(center_[0]-spatial_size[0]/2):int(center_[0]+spatial_size[0]/2), 
                                         int(center_[1]-spatial_size[1]/2):int(center_[1]+spatial_size[1]/2),
                                         int(zmin):int(zmax)]
                    if channel_dim is not None:
                        crop_data = np.expand_dims(crop_data, axis=channel_dim)
                        crop_mask = np.expand_dims(crop_mask, axis=channel_dim)
                    cropped_data.append(crop_data)
                    cropped_mask.append(crop_mask)
                
            d[key] = crop_data
            d[self.mask_key] = crop_mask
            d[self.label_key] = select_labels #!Not correct
        return d


