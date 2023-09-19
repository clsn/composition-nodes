from math import sqrt, pi as PI
from typing import Literal

import PIL.Image
import torch
from torchvision.transforms.functional import to_pil_image as pil_image_from_tensor

from invokeai.app.models.image import ImageCategory, ResourceOrigin
from invokeai.app.invocations.primitives import (
    ImageField,
    ImageOutput,
)

from invokeai.backend.stable_diffusion.diffusers_pipeline import (
    image_resized_to_grid_as_tensor,
)
from invokeai.app.invocations.baseinvocation import (
    BaseInvocation,
    BaseInvocationOutput,
    InputField,
    InvocationContext,
    invocation,
    invocation_output,
    OutputField
)

MASK_TYPES: list = [
    "shadows",
    "highlights",
    "midtones"
]

CIELAB_CHANNELS: list = ["L", "A", "B"]


@invocation_output("shmmask_output")
class ShadowsHighlightsMidtonesMasksOutput(BaseInvocationOutput):
    highlights_mask: ImageField = OutputField(default=None, description="Soft-edged highlights mask")
    midtones_mask: ImageField = OutputField(default=None, description="Soft-edged midtones mask")
    shadows_mask: ImageField = OutputField(default=None, description="Soft-edged shadows mask")
    width: int = OutputField(description="Width of the input/outputs")
    height: int = OutputField(description="Height of the input/outputs")


@invocation(
    "lab_channel",
    title="Extract CIELAB Channel",
    tags=["image", "channel", "mask", "cielab", "lab"],
    category="image",
    version="1.0.0",    
)
class ExtractCIELABChannelInvocation(BaseInvocation):
    """Get a selected channel from L*a*b* color space"""

    image: ImageField = InputField(description="Image from which to get channel")
    channel: Literal[tuple(CIELAB_CHANNELS)] = InputField(default=CIELAB_CHANNELS[0], description="Channel to extract")

    def invoke(self, context: InvocationContext) -> ImageOutput:
        image_in = context.services.images.get_pil_image(self.image.image_name)

        image_out = image_in.convert("LAB")
        image_out = image_out.getchannel(self.channel)
        
        image_dto = context.services.images.create(
            image=image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        return ImageOutput(
            image=ImageField(image_name=image_dto.image_name),
            width=image_dto.width,
            height=image_dto.height
        )
        

@invocation(
    "img_squash_lab_channel",
    title="Squash Image Channel (CIELAB)",
    tags=["image", "hue", "chroma", "accessibility"],
    category="image",
    version="1.0.0",
)
class ImageSquashLABChannelInvocation(BaseInvocation):
    """Adjusts the Hue of an image by rotating it in CIELAB L*C*h polar coordinates"""
    
    image: ImageField = InputField(description="The image to adjust")
    squash_a_pos: bool = InputField(default=False, description="Squash a* channel +")
    squash_a_neg: bool = InputField(default=False, description="Squash a* channel -")
    squash_b_pos: bool = InputField(default=False, description="Squash b* channel +")
    squash_b_neg: bool = InputField(default=False, description="Squash b* channel -")

    def invoke(self, context: InvocationContext) -> ImageOutput:
        image_in = context.services.images.get_pil_image(self.image.image_name)
        
        image_out = image_in.convert("LAB")
        channel_l = image_out.getchannel("L")
        channel_a = image_out.getchannel("A")
        channel_b = image_out.getchannel("B")

        l_tensor = image_resized_to_grid_as_tensor(channel_l, normalize=False)
        a_tensor = image_resized_to_grid_as_tensor(channel_a, normalize=True)
        b_tensor = image_resized_to_grid_as_tensor(channel_b, normalize=True)

        if (not self.squash_a_pos) and (not self.squash_a_neg):
            pass
        else:
            if self.squash_a_pos and self.squash_a_neg:
                a_tensor = torch.zeros(a_tensor.shape)
            else:
                neutral_tensor = torch.zeros(a_tensor.shape)
                mask = None
                if self.squash_a_pos:
                    mask = torch.gt(a_tensor, 0.0)
                else:  # self.squash_a_neg (implied):
                    mask = torch.lt(a_tensor, 0.0)
                a_tensor[mask] = neutral_tensor[mask]
        if (not self.squash_b_pos) and (not self.squash_b_neg):
            pass
        else:
            if self.squash_b_pos and self.squash_b_neg:
                b_tensor = torch.zeros(b_tensor.shape)
            else:
                neutral_tensor = torch.zeros(b_tensor.shape)
                mask = None
                if self.squash_b_pos:
                    mask = torch.gt(b_tensor, 0.0)
                else:  # self.squash_b_neg (implied):
                    mask = torch.lt(b_tensor, 0.0)
                b_tensor[mask] = neutral_tensor[mask]

        # -1..1 -> 0..1 for all elts of a, b
        a_tensor = torch.div(torch.add(a_tensor, 1.0), 2.0)
        b_tensor = torch.div(torch.add(b_tensor, 1.0), 2.0)
                
        # if not self.squash_b:
        #     b_tensor = torch.div(torch.add(b_tensor, 1.0), 2.0)
        # else:
        #     b_tensor = torch.mul(torch.ones(b_tensor.shape), 0.5)

        l_img = pil_image_from_tensor(l_tensor)
        a_img = pil_image_from_tensor(a_tensor)
        b_img = pil_image_from_tensor(b_tensor)
        
        image_out = PIL.Image.merge("LAB", (l_img, a_img, b_img))
        image_out = image_out.convert("RGB")
        image_dto = context.services.images.create(
            image=image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        return ImageOutput(
            image=ImageField(image_name=image_dto.image_name),
            width=image_dto.width,
            height=image_dto.height
        )

    
@invocation(
    "ealightness",
    title="Equivalent Achromatic Lightness",
    tags=["image", "channel", "mask", "cielab", "lab"],
    category="image",
    version="1.0.0",
)
class EquivalentAchromaticLightnessInvocation(BaseInvocation):
    """Calculate Equivalent Achromatic Lightness from image"""

    image: ImageField = InputField(description="Image from which to get channel")

    #  The chroma, C*
    #, and the hue, h, in the CIELAB color space are obtained by C*=sqrt((a*)^2+(b*)^2)
    #  and h=arctan(b*/a*)
    # k 0.1644	0.0603	0.1307	0.0060
    
    def invoke(self, context: InvocationContext) -> ImageOutput:
        image_in = context.services.images.get_pil_image(self.image.image_name)

        image_out = image_in.convert("LAB")
        channel_l = image_out.getchannel("L")
        channel_a = image_out.getchannel("A")
        channel_b = image_out.getchannel("B")

        l_tensor = image_resized_to_grid_as_tensor(channel_l, normalize=False)
        a_tensor = image_resized_to_grid_as_tensor(channel_a, normalize=True)
        b_tensor = image_resized_to_grid_as_tensor(channel_b, normalize=True)

        c_tensor = torch.sqrt(torch.add(torch.pow(a_tensor, 2), torch.pow(b_tensor, 2)))
        h_tensor = torch.atan2(b_tensor, a_tensor)

        k = [0.1644, 0.0603, 0.1307, 0.0060]

        h_minus_90 = torch.sub(h_tensor, PI / 2.0)
        h_minus_90 = torch.sub(torch.remainder(torch.add(h_minus_90, 3*PI), 2*PI), PI)

        f_by = torch.add(k[0] * torch.abs(torch.sin(torch.div(h_minus_90, 2.0))), k[1])
        f_r_0 = torch.add(k[2] * torch.abs(torch.cos(h_tensor)), k[3])

        f_r = torch.zeros(l_tensor.shape)
        mask_hi = torch.ge(h_tensor, -1 * (PI / 2.0))
        mask_lo = torch.le(h_tensor, PI / 2.0)
        mask = torch.logical_and(mask_hi, mask_lo)
        f_r[mask] = f_r_0[mask]
        
        image_tensor = torch.add(l_tensor, torch.tensordot(torch.add(f_by, f_r), c_tensor, dims=([1, 2], [1, 2])))
        image_tensor = torch.sub(image_tensor, image_tensor.min())

        image_out = pil_image_from_tensor(image_tensor)
        
        image_dto = context.services.images.create(
            image=image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        return ImageOutput(
            image=ImageField(image_name=image_dto.image_name),
            width=image_dto.width,
            height=image_dto.height
        )

    
   
@invocation(
    "shmmask",
    title="Shadows/Highlights/Midtones Mask from Image",
    tags=["mask", "image", "shadows", "highlights", "midtones"],
    category="image",
    version="1.0.0",
)
class ShadowsHighlightsMidtonesMaskInvocation(BaseInvocation):
    """Extract a Shadows/Highlights/Midtones mask from an image"""

    image: ImageField = InputField(description="Image from which to extract mask")
    highlight_threshold: float = InputField(default=0.75, description="Threshold beyond which mask values will be at extremum")
    upper_mid_threshold: float = InputField(default=0.7, description="Threshold to which to extend mask border by 0..1 gradient")
    lower_mid_threshold: float = InputField(default=0.3, description="Threshold to which to extend mask border by 0..1 gradient")
    shadow_threshold: float = InputField(default=0.25, description="Threshold beyond which mask values will be at extremum")

    def get_highlights_mask(self, image_tensor):
        img_tensor = image_tensor.clone()
        threshold_h, threshold_s = self.highlight_threshold, self.upper_mid_threshold
        ones_tensor = torch.ones(img_tensor.shape)
        zeros_tensor = torch.zeros(img_tensor.shape)

        zeros_mask = torch.ge(img_tensor, threshold_h)
        ones_mask = torch.lt(img_tensor, threshold_s)
        if not (threshold_h == threshold_s):
            mask_hi = torch.ge(img_tensor, threshold_s)
            mask_lo = torch.lt(img_tensor, threshold_h)
            mask = torch.logical_and(mask_hi, mask_lo)
            masked = img_tensor[mask]
            if 0 < masked.numel():
              vmax, vmin = masked.max(), masked.min()
              if (vmax == vmin):
                  img_tensor[mask] = 0.5 * ones_tensor
              else:
                  img_tensor[mask] = torch.sub(1.0, (img_tensor[mask] - vmin) / (vmax - vmin)) # hi is 0

        img_tensor[ones_mask] = ones_tensor[ones_mask]
        img_tensor[zeros_mask] = zeros_tensor[zeros_mask]

        return img_tensor


    def get_shadows_mask(self, image_tensor):
        img_tensor = image_tensor.clone()
        threshold_h, threshold_s = self.shadow_threshold, self.lower_mid_threshold
        ones_tensor = torch.ones(img_tensor.shape)
        zeros_tensor = torch.zeros(img_tensor.shape)

        zeros_mask = torch.le(img_tensor, threshold_h)
        ones_mask = torch.gt(img_tensor, threshold_s)
        if not (threshold_h == threshold_s):
            mask_hi = torch.le(img_tensor, threshold_s)
            mask_lo = torch.gt(img_tensor, threshold_h)
            mask = torch.logical_and(mask_hi, mask_lo)
            masked = img_tensor[mask]
            if 0 < masked.numel():
                vmax, vmin = masked.max(), masked.min()
                if (vmax == vmin):
                    img_tensor[mask] = 0.5 * ones_tensor
                else:
                    img_tensor[mask] = (img_tensor[mask] - vmin) / (vmax - vmin) # lo is 0

        img_tensor[ones_mask] = ones_tensor[ones_mask]
        img_tensor[zeros_mask] = zeros_tensor[zeros_mask]

        return img_tensor


    def get_midtones_mask(self, image_tensor):
        img_tensor = image_tensor.clone()
        h_threshold_hard, h_threshold_soft = self.highlight_threshold, self.upper_mid_threshold
        s_threshold_hard, s_threshold_soft = self.shadow_threshold, self.lower_mid_threshold
        ones_tensor = torch.ones(img_tensor.shape)
        zeros_tensor = torch.zeros(img_tensor.shape)

        mask_lo = torch.le(img_tensor, h_threshold_soft)
        mask_hi = torch.ge(img_tensor, s_threshold_soft)
        mid_mask = torch.logical_and(mask_hi, mask_lo)
        highlight_ones_mask = torch.gt(img_tensor, h_threshold_hard)
        shadows_ones_mask = torch.lt(img_tensor, s_threshold_hard)
        mask_top_hi = torch.gt(img_tensor, h_threshold_soft)
        mask_top_lo = torch.le(img_tensor, h_threshold_hard)
        mask_top = torch.logical_and(mask_top_hi, mask_top_lo)
        mask_bottom_hi = torch.ge(img_tensor, s_threshold_hard)
        mask_bottom_lo = torch.lt(img_tensor, s_threshold_soft)
        mask_bottom = torch.logical_and(mask_bottom_hi, mask_bottom_lo)

        if not (h_threshold_hard == h_threshold_soft):
            masked = img_tensor[mask_top]
            if 0 < masked.numel():
                vmax_top, vmin_top = masked.max(), masked.min()
                if (vmax_top == vmin_top):
                    img_tensor[mask_top] = 0.5 * ones_tensor
                else:
                    img_tensor[mask_top] = (img_tensor[mask_top] - vmin_top) / (vmax_top - vmin_top) # hi is 1
            
        if not (s_threshold_hard == s_threshold_soft):
            masked = img_tensor[mask_bottom]
            if 0 < masked.numel():
                vmax_bottom, vmin_bottom = masked.max(), masked.min()
                if (vmax_bottom == vmin_bottom):
                    img_tensor[mask_bottom] = 0.5 * ones_tensor
                else:
                    img_tensor[mask_bottom] = torch.sub(1.0, (img_tensor[mask_bottom] - vmin_bottom) / (vmax_bottom - vmin_bottom)) # lo is 1

        img_tensor[mid_mask] = zeros_tensor[mid_mask]
        img_tensor[highlight_ones_mask] = ones_tensor[highlight_ones_mask]
        img_tensor[shadows_ones_mask] = ones_tensor[shadows_ones_mask]

        return img_tensor


    def invoke(self, context: InvocationContext) -> ShadowsHighlightsMidtonesMasksOutput:
        image_in = context.services.images.get_pil_image(self.image.image_name)
        if image_in.mode != "L":
            image_in = image_in.convert("L")
        image_tensor = image_resized_to_grid_as_tensor(image_in, normalize=False)
        h_image_out = pil_image_from_tensor(self.get_highlights_mask(image_tensor), mode="L")
        h_image_dto = context.services.images.create(
            image=h_image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        m_image_out = pil_image_from_tensor(self.get_midtones_mask(image_tensor), mode="L")
        m_image_dto = context.services.images.create(
            image=m_image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        s_image_out = pil_image_from_tensor(self.get_shadows_mask(image_tensor), mode="L")
        s_image_dto = context.services.images.create(
            image=s_image_out,
            image_origin=ResourceOrigin.INTERNAL,
            image_category=ImageCategory.GENERAL,
            node_id=self.id,
            session_id=context.graph_execution_state_id,
            is_intermediate=self.is_intermediate
        )
        return ShadowsHighlightsMidtonesMasksOutput(
            highlights_mask=ImageField(image_name=h_image_dto.image_name),
            midtones_mask=ImageField(image_name=m_image_dto.image_name),
            shadows_mask=ImageField(image_name=s_image_dto.image_name),
            width=h_image_dto.width,
            height=h_image_dto.height
        )