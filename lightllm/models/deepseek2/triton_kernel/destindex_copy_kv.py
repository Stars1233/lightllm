import torch

import triton
import triton.language as tl


def _is_power_of_two(n):
    return n > 0 and (n & (n - 1)) == 0


@triton.jit
def _fwd_kernel_destindex_copy_kv(
    KV_nope,
    KV_rope,
    KV_scale,
    Dest_loc,
    O_nope,
    O_rope,
    O_scale,
    stride_kv_nope_bs,
    stride_kv_nope_h,
    stride_kv_nope_d,
    stride_kv_rope_bs,
    stride_kv_rope_h,
    stride_kv_rope_d,
    stride_o_nope_bs,
    stride_o_nope_h,
    stride_o_nope_d,
    stride_o_rope_bs,
    stride_o_rope_h,
    stride_o_rope_d,
    kv_nope_head_num,
    kv_rope_head_num,
    HAS_SCALE: tl.constexpr,
    BLOCK_DMODEL_NOPE: tl.constexpr,
    BLOCK_DMODEL_ROPE: tl.constexpr,
):
    cur_index = tl.program_id(0)
    offs_d_nope = tl.arange(0, BLOCK_DMODEL_NOPE)
    offs_d_rope = tl.arange(0, BLOCK_DMODEL_ROPE)

    dest_index = tl.load(Dest_loc + cur_index)

    kv_nope_ptrs = KV_nope + cur_index * stride_kv_nope_bs + stride_kv_nope_d * offs_d_nope[None, :]
    kv_rope_ptrs = KV_rope + cur_index * stride_kv_rope_bs + stride_kv_rope_d * offs_d_rope[None, :]

    o_nope_ptrs = O_nope + dest_index * stride_o_nope_bs + stride_o_nope_d * offs_d_nope[None, :]
    o_rope_ptrs = O_rope + dest_index * stride_o_rope_bs + stride_o_rope_d * offs_d_rope[None, :]

    kv_nope = tl.load(kv_nope_ptrs)
    kv_rope = tl.load(kv_rope_ptrs)

    if HAS_SCALE:
        offs_d_scale = tl.arange(0, 2)
        o_scale_ptrs = O_scale + dest_index * stride_o_rope_bs + stride_o_rope_d * offs_d_scale[None, :]
        kv_scale_ptrs = KV_scale + cur_index * 2 + offs_d_scale[None, :]
        kv_scale = tl.load(kv_scale_ptrs)
        tl.store(o_scale_ptrs, kv_scale)

    tl.store(o_nope_ptrs, kv_nope)
    tl.store(o_rope_ptrs, kv_rope)
    return


@torch.no_grad()
def destindex_copy_kv(KV_nope, KV_rope, DestLoc, O_nope, O_rope, O_scale=None, KV_scale=None):
    seq_len = DestLoc.shape[0]
    kv_nope_head_num = KV_nope.shape[1]
    kv_rope_head_num = KV_rope.shape[1]

    kv_nope_head_dim = KV_nope.shape[2]
    kv_rope_head_dim = KV_rope.shape[2]

    assert KV_nope.shape[1] == O_nope.shape[1]
    assert KV_nope.shape[2] == O_nope.shape[2]
    assert KV_rope.shape[1] == O_rope.shape[1]
    assert KV_rope.shape[2] == O_rope.shape[2]
    grid = (seq_len,)
    num_warps = 1

    _fwd_kernel_destindex_copy_kv[grid](
        KV_nope,
        KV_rope,
        KV_scale,
        DestLoc,
        O_nope,
        O_rope,
        O_scale,
        KV_nope.stride(0),
        KV_nope.stride(1),
        KV_nope.stride(2),
        KV_rope.stride(0),
        KV_rope.stride(1),
        KV_rope.stride(2),
        O_nope.stride(0),
        O_nope.stride(1),
        O_nope.stride(2),
        O_rope.stride(0),
        O_rope.stride(1),
        O_rope.stride(2),
        kv_nope_head_num,
        kv_rope_head_num,
        HAS_SCALE=KV_scale is not None,
        BLOCK_DMODEL_NOPE=kv_nope_head_dim,
        BLOCK_DMODEL_ROPE=kv_rope_head_dim,
        num_warps=num_warps,
        num_stages=1,
    )
    return


def test1():
    B, N_CTX, H, H1, D, D1 = 32, 1024, 12, 1, 128, 64
    KV_nope = torch.randn((B * N_CTX, H, D), dtype=torch.float16).cuda()
    KV_rope = torch.randn((B * N_CTX, H1, D1), dtype=torch.float16).cuda()
    dest_loc = torch.arange(0, B * N_CTX, dtype=torch.int32, device="cuda")
    O_nope = torch.randn((B * N_CTX, H, D), dtype=torch.float16).cuda()
    O_rope = torch.randn((B * N_CTX, H1, D1), dtype=torch.float16).cuda()

    destindex_copy_kv(KV_nope, KV_rope, dest_loc, O_nope, O_rope)

    print("max ", torch.max(torch.abs(O_nope - KV_nope)))
    print("mean ", torch.mean(torch.abs(O_nope - KV_nope)))
    assert torch.allclose(O_nope, KV_nope, atol=1e-2, rtol=0)
    print("max ", torch.max(torch.abs(O_rope - KV_rope)))
    print("mean ", torch.mean(torch.abs(O_rope - KV_rope)))
    assert torch.allclose(O_rope, KV_rope, atol=1e-2, rtol=0)


if __name__ == "__main__":
    test1()
