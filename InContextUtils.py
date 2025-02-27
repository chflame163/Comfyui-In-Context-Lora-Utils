
import torch
import numpy as np
import cv2

def resize(img,resolution,interpolation=cv2.INTER_CUBIC):
    return cv2.resize(img,resolution, interpolation=interpolation)

def create_image_from_color(width, height, color=(255, 255, 255)):
    # OpenCV uses BGR, so convert hex color to BGR if necessary
    if isinstance(color, str) and color.startswith('#'):
        color = tuple(int(color[i:i+2], 16) for i in (5, 3, 1))[::-1]
        
    # Create a blank image with the specified color
    blank_image = np.full((height, width, 3), color, dtype=np.uint8)
    return blank_image

def closest_mod_64(value):
    return value - (value % 64)

def get_target_width_height(image, output_length, patch_mode, patch_type):
    if output_length % 64 != 0:
        output_length = output_length - (output_length % 64)
    
    # image = image.detach().cpu().numpy()
    image_height, image_width, _ = image.shape
    
    patch_ratio = [int(x) for x in patch_type.split(":")]
    short_part = patch_ratio[0]
    long_part = patch_ratio[1]
    total = short_part * 2

    if (patch_mode == "auto" and image_width > image_height) or patch_mode == "patch_bottom":
        patch_mode = "patch_bottom"
        target_width = int(output_length / total * long_part)
        target_height = int(output_length / total * short_part)
    else:
        patch_mode = "patch_right"
        target_width = int(output_length / total * short_part)
        target_height = int(output_length / total * long_part)
    
    return output_length, patch_mode, target_width, target_height

class GetWidthHeightAndMode:
    @classmethod
    def INPUT_TYPES(s):
        return {
                    "required": { 
                        "image": ("IMAGE",),
                        "output_length": ("INT", {
                            "default": 1536,
                        }),
                        "patch_mode": (["auto", "patch_right", "patch_bottom"], {
                            "default": "auto",
                        }),
                        "patch_type": (["1:1", "4:3", "9:16"], {
                            "default": "1:1",
                        }),
                    }
        }
    RETURN_TYPES = ("INT",  "STRING", "INT", "INT")
    RETURN_NAMES = ("output_length", "patch_mode", "target_width", "target_height")
    FUNCTION = "get_mode"
    CATEGORY = "ICEditUtils/GetWidthHeightAndMode"
    def get_mode(self, image, output_length, patch_mode, patch_type):
        output_length, patch_mode, target_width, target_height = get_target_width_height(image, output_length, patch_mode, patch_type)
        return (output_length, patch_mode, target_width, target_height, patch_type, )
    

def get_padding(image, target_width, target_height):
    image_height, image_width, _ = image.shape
    # should scale down the target size to image level
    scale = 1
    if image_height > image_width:
        scale = (target_height/image_height)
        new_width = int(image_width*scale)
        new_height = target_height
    else:
        scale = (target_width/image_width)
        new_width = target_width
        new_height = int(image_height*scale)
    # print("image_width, image_height", image_width, image_height)
    # print("target_width, target_height", target_width, target_height)
    # print("new_width, new_height", new_width, new_height)
    # print("scale", scale)
    diff_x = (target_width - new_width) / scale
    diff_y = (target_height - new_height) / scale
    pad_x = diff_x // 2
    pad_y = diff_y // 2
    return pad_x, pad_y

def get_cropping(image, target_width, target_height):
    # get resized image size
    image_height, image_width, _ = image.shape
    # print("new size",image_height, image_width)
    # simple center crop
    scale_ratio = target_width / target_height
    image_ratio = image_width / image_height
    # referenced kohya ss code
    if image_ratio > scale_ratio: 
        up_scale = image_height / target_height
    else:
        up_scale = image_width / target_width
    expanded_closest_size = (int(target_width * up_scale + 0.5), int(target_height * up_scale + 0.5))
    diff_x = (expanded_closest_size[0] - image_width) / up_scale
    diff_y = (expanded_closest_size[1] - image_height) / up_scale
    
    crop_x =  diff_x // 2
    crop_y =  diff_y // 2    
    return crop_x, crop_y
    
# make the perfect mask for in context lora
# scale the mask to maximum 4x and minium 0.25x
# full pixel usage with 768x1024 context window
class CreateContextWindow:
    @classmethod
    def INPUT_TYPES(s):
        return {
                "required": { 
                    "input_image": ("IMAGE",),
                    "input_mask": ("MASK",),
                    "patch_mode": (["auto", "patch_right", "patch_bottom"], {
                        "default": "auto",
                    }),
                    "patch_type": (["3:4","1:1", "9:16"], {
                        "default": "3:4",
                    }),
                    
                },
                "optional":{
                    "output_length": ("INT", {
                        "default": 1536,
                    }),
                    "pixel_buffer": ("INT", {
                        "default": 64,
                    }),
                }
            }
    RETURN_TYPES = ("IMAGE", "MASK",  "STRING", "INT", "INT", "FLOAT", "IMAGE", "MASK")
    RETURN_NAMES = ("prepared_image", "prepared_mask", "patch_mode", "x_offset_of_ori", "y_offset_of_ori", "scale", "crop_area", "crop_mask")
    FUNCTION = "create_context_window"
    CATEGORY = "InContextUtils/CreateContextWindow"
    
    
    def create_context_window(self, input_image, input_mask, patch_mode, patch_type,output_length=1536, pixel_buffer=64):
        image = input_image[0].clone()
        mask = input_mask[0].clone()
        image = image.detach().cpu().numpy()
        mask = mask.detach().cpu().numpy()
        
        # Convert the binary mask to 8-bit
        mask = (mask > 0).astype(np.uint8)

        # Alternatively, using OpenCV (if your mask is not binary, i.e., has non-1/0 values):
        mask = cv2.convertScaleAbs(mask)
        if output_length % 64 != 0:
                output_length = output_length - (output_length % 64)
        output_length, patch_mode, target_width, target_height = get_target_width_height(image, output_length, patch_mode, patch_type)
        
        image_height, image_width, _ = image.shape
        # Step 1: Find contours of the "1" shape
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        
        if input_mask is None or np.all(mask == 0) or contours[0] is None:
            print("Mask is not found. Return original image with mask all ones")
            image1 = input_image[0].clone()
            image1, image1_mask, target_width, target_height, patch_mode = fit_image(image1,None,output_length,patch_mode,patch_type)
        
            print("image1.shape",image1.shape)
            print("image1_mask.shape",image1_mask.shape)
            image1 = np.clip(255. * image1, 0, 255).astype(np.float32) / 255.0
            image1 = torch.from_numpy(image1)[None,]
            image1_mask = torch.from_numpy(image1_mask)[None,]
            return (image1, image1_mask, patch_mode, 0, 0, 1, image1, image1_mask, )
        
        # Assume there is only one shape of interest
        contour = contours[0]
        # Step 2: Calculate the bounding box (x, y, width, height)
        ori_x, ori_y, ori_bb_width, ori_bb_height = cv2.boundingRect(contour)
        
        # get center of the bounding box
        center_x, center_y = ori_x + ori_bb_width // 2, ori_y + ori_bb_height // 2
        # print("center_x, center_y", center_x, center_y)
        
        ori_x_with_buffer = max(int(ori_x - pixel_buffer//2), 0)
        ori_y_with_buffer = max(int(ori_y - pixel_buffer//2), 0)
        buffer_bb_width = min(int(ori_bb_width + pixel_buffer), image_width)
        buffer_bb_height = min(int(ori_bb_height + pixel_buffer), image_height)
        crop_image_part = image[ori_y_with_buffer:ori_y_with_buffer + buffer_bb_height, ori_x_with_buffer:ori_x_with_buffer + buffer_bb_width]
        crop_mask_part = mask[ori_y_with_buffer:ori_y_with_buffer + buffer_bb_height, ori_x_with_buffer:ori_x_with_buffer + buffer_bb_width]
        
        patch_ratio = [int(x) for x in patch_type.split(":")]
        short_part = patch_ratio[0]
        long_part = patch_ratio[1]
        total = short_part * 2
        crop_image_height, crop_image_width, _ = crop_image_part.shape
        # print("ori crop_image_width, crop_image_height", crop_image_width, crop_image_height)
        
        if crop_image_width >= crop_image_height:
            if (patch_mode == "auto" and image_width > image_height) or patch_mode == "patch_bottom":
                patch_mode = "patch_bottom"
                crop_output_length = int(crop_image_width / long_part * total)
                crop_image_height = int(crop_output_length / total * short_part)
            else:
                patch_mode = "patch_right"
                crop_output_length = int(crop_image_width / short_part * total)
                crop_image_height = int(crop_output_length / total * long_part)
        else:
            if (patch_mode == "auto" and image_width > image_height) or patch_mode == "patch_bottom":
                patch_mode = "patch_bottom"
                crop_output_length = int(crop_image_height / short_part * total)
                crop_image_width = int(crop_output_length / total * long_part)
            else:
                patch_mode = "patch_right"
                crop_output_length = int(crop_image_height / long_part * total)
                crop_image_width = int(crop_output_length / total * short_part)
        
        # based on center x,y and crop image width, calculate the x,y offset
        new_x = int(center_x - crop_image_width // 2)
        new_y = int(center_y - crop_image_height // 2)
        
        # print("new_x,new_y", new_x,new_y)
        # print("crop_output_length", crop_output_length)
        # print("crop_image_width, crop_image_height", crop_image_width, crop_image_height)
        
        if new_y+crop_image_height > image_height:
            new_y = image_height - crop_image_height
        if new_x+crop_image_width > image_width:
            new_x = image_width - crop_image_width
        if new_x < 0:
            new_x = 0
        if new_y < 0:
            new_y = 0
            
        
        fit_image_part = image[new_y:new_y+crop_image_height, new_x:new_x+crop_image_width]
        fit_mask_part = mask[new_y:new_y+crop_image_height, new_x:new_x+crop_image_width]
        
        up_scale = crop_image_width / target_width
        
        resized_image_part = resize(fit_image_part, (target_width,target_height))
        resized_mask_part = resize(fit_mask_part, (target_width,target_height), cv2.INTER_NEAREST_EXACT)
        
        resized_image_part = np.clip(255. * resized_image_part, 0, 255).astype(np.float32) / 255.0
        resized_image_part = torch.from_numpy(resized_image_part)[None,]
        resized_mask_part = torch.from_numpy(resized_mask_part)[None,]
        
        fit_image_part = np.clip(255. * fit_image_part, 0, 255).astype(np.float32) / 255.0
        fit_image_part = torch.from_numpy(fit_image_part)[None,]
        fit_mask_part = torch.from_numpy(fit_mask_part)[None,]
        
        # output_patch_mode = (["auto", "patch_right", "patch_bottom"], {
        #     "default": patch_mode,
        # })
        return (resized_image_part, resized_mask_part, patch_mode, new_x, new_y, up_scale, fit_image_part, fit_mask_part, )
def fit_image(image,mask=None,output_length=1536,patch_mode="auto",patch_type="3:4",target_width=None,target_height=None):
    if torch.is_tensor(image):
        image = image.detach().cpu().numpy()
    if mask is not None:
        if torch.is_tensor(mask):
            mask = mask.detach().cpu().numpy()
    image_height, image_width, _ = image.shape
    if target_width is None or target_height is None:
        output_length, patch_mode, target_width, target_height = get_target_width_height(image, output_length, patch_mode, patch_type)
    
    # up_scale = 1
    # pad_x = 0
    # pad_y = 0
    # crop_x = 0
    # crop_y = 0
    if image_width < target_width or image_height < target_height:
        # print("image too small, resize to ", target_width, target_height)
        if image_height > image_width:
            new_width = int(image_width*(target_height/image_height))
            new_height = target_height
            # print(new_width,new_height)
            image = resize(image, (new_width,new_height))
            # print("mask",mask)
            # print("mask.shape",mask.shape)
            if mask is not None:
                mask = resize(mask, (new_width,new_height),cv2.INTER_NEAREST_EXACT)
        else:
            new_width = target_width
            new_height = int(image_height*(target_width/image_width))
            # print(new_width,new_height)
            image = resize(image, (new_width,new_height))
            
            # print("mask",mask)
            # print("mask.shape",mask.shape)
            if mask is not None:
                mask = resize(mask, (new_width,new_height),cv2.INTER_NEAREST_EXACT)
            
        image_height, image_width, _ = image.shape
        
        diff_x = target_width - image_width
        # print("diff_x",diff_x)
        diff_y = target_height - image_height
        # print("diff_y",diff_y)
        pad_x = abs(diff_x) // 2
        pad_y = abs(diff_y) // 2
        # add white pixels for padding
        if diff_x > 0 or diff_y > 0:
            resized_image = cv2.copyMakeBorder(
                image,
                pad_y, abs(diff_y) - pad_y,
                pad_x, abs(diff_x) - pad_x,
                cv2.BORDER_CONSTANT, value=(255, 255, 255)
            )
            if mask is not None:
                resized_mask = cv2.copyMakeBorder(
                    mask,
                    pad_y, abs(diff_y) - pad_y,
                    pad_x, abs(diff_x) - pad_x,
                    cv2.BORDER_CONSTANT, value=(0, 0, 0)
                )
        # crop extra pixels for square
        else:
            resized_image = image[pad_y:image_height-pad_y, pad_x:image_width-pad_x]
            if mask is not None:
                resized_mask = mask[pad_y:image_height-pad_y, pad_x:image_width-pad_x]
    else:
        # get resized image size
        image_height, image_width, _ = image.shape
        # print("new size",image_height, image_width)
        # simple center crop
        scale_ratio = target_width / target_height
        image_ratio = image_width / image_height
        # referenced kohya ss code
        if image_ratio > scale_ratio: 
            up_scale = image_height / target_height
        else:
            up_scale = image_width / target_width
        expanded_closest_size = (int(target_width * up_scale + 0.5), int(target_height * up_scale + 0.5))
        diff_x = abs(expanded_closest_size[0] - image_width)
        diff_y = abs(expanded_closest_size[1] - image_height)
        
        crop_x =  diff_x // 2
        crop_y =  diff_y // 2
        cropped_image = image[crop_y:image_height-crop_y, crop_x:image_width-crop_x]
        resized_image = resize(cropped_image, (target_width,target_height))
        
        if mask is not None:
            # print("mask",mask)
            # print("mask.shape",mask.shape)
            cropped_mask = mask[crop_y:image_height-crop_y, crop_x:image_width-crop_x]
            resized_mask = resize(cropped_mask, (target_width,target_height),cv2.INTER_NEAREST_EXACT)

    if mask is None:
        resized_mask = torch.ones((target_height,target_width))
    
    if torch.is_tensor(resized_image):
        resized_image = resized_image.detach().cpu().numpy()
    if torch.is_tensor(resized_mask):
        resized_mask = resized_mask.detach().cpu().numpy()
    
    print("torch.is_tensor(resized_image)",torch.is_tensor(resized_image))
    print("torch.is_tensor(resized_mask)",torch.is_tensor(resized_mask))
    return resized_image, resized_mask, target_width, target_height, patch_mode

class ConcatContextWindow:
    @classmethod
    def INPUT_TYPES(s):
        return {
                    "required": { 
                        "first_image": ("IMAGE",),
                        "patch_mode": (["auto", "patch_right", "patch_bottom"], {
                            "default": "auto",
                        }),
                        "patch_type": (["3:4","1:1", "9:16"], {
                            "default": "3:4",
                        }),
                        "output_length": ("INT", {
                            "default": 1536,
                        }),
                        "patch_color": (["#FF0000", "#00FF00","#0000FF", "#FFFFFF"], {
                            "default": "#FF0000",
                        }),
                    },
                    "optional":{
                        # "first_mask": ("MASK",),
                        "second_image": ("IMAGE",),
                        "second_mask": ("MASK",),
                    }
                }
    RETURN_TYPES = ("IMAGE", "MASK", "INT", "INT", "INT", "INT", "INT", "INT")
    RETURN_NAMES = ("IMAGE", "MASK", "target_width", "target_height", "x_offset", "y_offset", "total_width", "total_height")
    FUNCTION = "concat_context_window"

    CATEGORY = "InContextUtils/ConcatContextWindow"
    def concat_context_window(self, first_image, patch_mode, patch_type, output_length, patch_color, second_image=None, second_mask=None):
        if output_length % 64 != 0:
            output_length = output_length - (output_length % 64)
        image1 = first_image[0].clone()
        image1, _, target_width, target_height, patch_mode = fit_image(image1, None, output_length, patch_mode, patch_type)
        
        image1_mask = torch.zeros((target_height,target_width))
        if second_image is None:
            # create blank image with patch color
            image2 = create_image_from_color(target_width,target_height, color=patch_color)
            image2 = torch.from_numpy(image2)
            if second_mask is None:
                image2_mask = torch.zeros((image2.shape[0], image2.shape[1]))
            else:
                image2_mask = second_mask[0].clone()
            image2,image2_mask,_,_,_ = fit_image(image2, image2_mask, output_length, patch_mode, patch_type)
        else:
            image2 = second_image[0]
            if second_mask is None:
                image2_mask = torch.ones((image1.shape[0], image1.shape[1]))
            else:
                image2_mask = second_mask[0].clone()
        min_y = 0
        min_x = 0
        
        # print("image1.shape",image1.shape)
        # print("image2.shape",image2.shape)
        # print("image1_mask.shape",image1_mask.shape)
        # print("image2_mask.shape",image2_mask.shape)
        if patch_mode == "patch_right":
            concatenated_image = np.hstack((image1, image2))
            concatenated_mask = np.hstack((image1_mask, image2_mask))
            min_x = 50
        else:
            concatenated_image = np.vstack((image1, image2))
            concatenated_mask = np.vstack((image1_mask, image2_mask))
            min_y = 50
        min_y = int(min_y / 100.0 * concatenated_image.shape[0])
        min_x = int(min_x / 100.0 * concatenated_image.shape[1])
        
        return_masks = torch.from_numpy(concatenated_mask)[None,]
        
        concatenated_image = np.clip(255. * concatenated_image, 0, 255).astype(np.float32) / 255.0
        concatenated_image = torch.from_numpy(concatenated_image)[None,]
        
        return_images = concatenated_image
        return (return_images, return_masks, target_width, target_height, min_x, min_y, concatenated_image.shape[1], concatenated_image.shape[0], )

# NODE_CLASS_MAPPINGS = {
#     "ConcatContextWindow": ConcatContextWindow,
#     "CreateContextWindow": CreateContextWindow
# }

# NODE_DISPLAY_NAME_MAPPINGS = {
#     "ConcatContextWindow": "Concatenate Context Window",
#     "CreateContextWindow": "Create Context Window",
# }