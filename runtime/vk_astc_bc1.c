// SPDX-License-Identifier: GPL-3.0-or-later
// The image of an ASTC texture kept as BC3 (TOTK_ASTC_BC) made again as BC1 when every block of the texture is opaque
// (astc_opaque.h), or back to BC3 when alpha shows up: the same extent, levels, layers, create flags and usage with
// the other format, in new memory, half or twice the video memory. The old image and the replay's views of it go
// once the GPU is done with them, as destroy_tex does them, and the entry is left describing the new image, so all
// its owner has to do is transition the new image, upload it whole and bump the generation. vk_astc.c encodes the
// BC1 blocks (bc1_encode.comp). The method is Hookshot's (gpu/vk/vk_texture.cpp bc_reformat, commit 97024ef5,
// GPL-3.0-or-later), written again in C for this renderer's images.
#include "host.h"
#include "vk.h"

int vk_astc_bc_remake(VkTexImage *img, VkImageCreateFlags flags, VkImageUsageFlags usage, int srgb, int to_bc1,
                      VmaAllocation *out_alloc, VkFormat *out_format, uint8_t *out_bc) {
    static volatile int32_t n_to_bc1, n_to_bc3, n_failed;
    VkCtx *c = vk_ctx();
    if (!c || img->type != VK_IMAGE_TYPE_2D || img->samples != 1) return 0;
    if (img->bound || !*out_alloc) return 0;   // pool memory: another image would alias the same pages
    VkFormat f = to_bc1 ? (srgb ? VK_FORMAT_BC1_RGBA_SRGB_BLOCK : VK_FORMAT_BC1_RGBA_UNORM_BLOCK)
                        : (srgb ? VK_FORMAT_BC3_SRGB_BLOCK : VK_FORMAT_BC3_UNORM_BLOCK);
    VkImageCreateInfo ci = {.sType = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO, .flags = flags, .imageType = img->type,
                            .format = f, .extent = {img->width, img->height, img->depth}, .mipLevels = img->levels,
                            .arrayLayers = img->layers, .samples = VK_SAMPLE_COUNT_1_BIT,
                            .tiling = VK_IMAGE_TILING_OPTIMAL, .usage = usage,
                            .initialLayout = VK_IMAGE_LAYOUT_UNDEFINED};
    VkImage new_img;
    VmaAllocation new_alloc;
    VmaAllocationCreateInfo ai = {.usage = VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE};
    if (vmaCreateImage(c->vma, &ci, &ai, &new_img, &new_alloc, NULL) != VK_SUCCESS) {
        int32_t k = __atomic_add_fetch(&n_failed, 1, __ATOMIC_RELAXED);
        if (k <= 4)
            rt_log("vk: ASTC texture %#llx %ux%u: its image could not be made again as %s; it stays as it is",
                   (unsigned long long)img->addr, img->width, img->height, to_bc1 ? "BC1" : "BC3");
        return 0;
    }
    // The old image and the replay's views of it go once the GPU is done with them. The new one is the entry's from
    // here on, so it is not deferred; it holds nothing and is still UNDEFINED, for the owner to lay out.
    vk_exec_image_gone(img->image);
    vk_defer_image(img->image, *out_alloc);
    img->image = new_img, img->format = f;
    *out_alloc = new_alloc, *out_format = f, *out_bc = (uint8_t)(to_bc1 ? 2 : 1);
    int32_t k = __atomic_add_fetch(to_bc1 ? &n_to_bc1 : &n_to_bc3, 1, __ATOMIC_RELAXED);
    if (k <= 4 || (k & (k - 1)) == 0)
        rt_log("vk: ASTC texture %#llx %ux%u x%u: %s (%d opaque textures kept as BC1, %d back to BC3; "
               "TOTK_ASTC_BC1=0: all BC3)", (unsigned long long)img->addr, img->width, img->height, img->layers,
               to_bc1 ? "every block opaque, kept as BC1" : "alpha in it now, BC3 again",
               (int)__atomic_load_n(&n_to_bc1, __ATOMIC_RELAXED), (int)__atomic_load_n(&n_to_bc3, __ATOMIC_RELAXED));
    return 1;
}
