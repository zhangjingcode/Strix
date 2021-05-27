import torch 
from medlp.models.cnn import ARCHI_MAPPING
from monai.data.synthetic import create_test_image_2d


model_type = ARCHI_MAPPING['classification']['2D']['HESAM']
model = model_type(
    dimensions=2,
    in_channels=1,
    out_channels=2,
    features=(32, 32, 64, 128, 256),
    last_feature=32,
)
input_data = torch.Tensor(4, 1, 64, 64)
print(model)
output = model(input_data)
print(output.shape)