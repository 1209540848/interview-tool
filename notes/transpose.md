# CUDA Transpose 两种实现

## 1. 基础版本：一个线程转置一个元素

思路：每个线程读取 `A[row][col]`，然后写到 `B[col][row]`。读取通常是连续的，但转置写回时地址不连续，Global Memory 写入的 coalescing 较差。

```cpp
#include <cuda_runtime.h>

__global__ void transpose_basic_kernel(
    const float* __restrict__ input,
    float* __restrict__ output,
    int M,
    int N)
{
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    int row = blockIdx.y * blockDim.y + threadIdx.y;

    if (row < M && col < N) {
        output[col * M + row] =
            input[row * N + col];
    }
}

extern "C" void transpose_basic(
    const float* input,
    float* output,
    int M,
    int N)
{
    dim3 block(32, 8);

    dim3 grid(
        (N + block.x - 1) / block.x,
        (M + block.y - 1) / block.y
    );

    transpose_basic_kernel<<<grid, block>>>(
        input,
        output,
        M,
        N
    );
}
```

---

## 2. 升级版本：Shared Memory Tile + Padding

思路：先把 `32 × 32` 的 Tile 连续读取到 Shared Memory，再交换坐标后连续写回 Global Memory，使读写都尽量保持 coalesced；Shared Memory 使用 `32 × 33`，通过 padding 避免转置访问时的 Bank Conflict。

```cpp
#include <cuda_runtime.h>

#define TILE_DIM 32
#define BLOCK_ROWS 8

__global__ void transpose_shared_kernel(
    const float* __restrict__ input,
    float* __restrict__ output,
    int M,
    int N)
{
    __shared__ float tile[TILE_DIM][TILE_DIM + 1];

    int x = blockIdx.x * TILE_DIM + threadIdx.x;
    int y = blockIdx.y * TILE_DIM + threadIdx.y;

    #pragma unroll
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < N && y + j < M) {
            tile[threadIdx.y + j][threadIdx.x] =
                input[(y + j) * N + x];
        }
    }

    __syncthreads();

    x = blockIdx.y * TILE_DIM + threadIdx.x;
    y = blockIdx.x * TILE_DIM + threadIdx.y;

    #pragma unroll
    for (int j = 0; j < TILE_DIM; j += BLOCK_ROWS) {
        if (x < M && y + j < N) {
            output[(y + j) * M + x] =
                tile[threadIdx.x][threadIdx.y + j];
        }
    }
}

extern "C" void transpose_shared(
    const float* input,
    float* output,
    int M,
    int N)
{
    dim3 block(
        TILE_DIM,
        BLOCK_ROWS
    );

    dim3 grid(
        (N + TILE_DIM - 1) / TILE_DIM,
        (M + TILE_DIM - 1) / TILE_DIM
    );

    transpose_shared_kernel<<<grid, block>>>(
        input,
        output,
        M,
        N
    );
}
```
