# Git 与 Docker 版本发布约定

本约定适用于基础镜像、PHP 扩展镜像、业务应用三层仓库。

## 分支与版本

- `main` 是持续开发主线；功能分支审查后合入 main，不使用 `3.1.6-up2` 等长期版本分支代替发布记录。
- 正式发布用带注释的 Git tag 固化已验收提交；Git tag 与 Docker tag 同名，例如 `3.1.6-up2`、`3.1.6-up3`。各仓库的同名 tag 指向各自的提交，不代表相同 SHA。
- 已发布 tag 不移动、不覆盖、不删除。修复后发布新版本。历史上本次 up3 曾获授权覆盖一次，不作为后续常规流程。
- 历史 up2 必须标记到 up2 的真实源码基线，不能标到已含 up3 改动的 main。来源不明时先核对镜像和提交，不凭最新分支名猜测。
- 基础和扩展采用统一版本；应用若使用自己的业务版本，也必须遵守 Git tag 与该应用 Docker tag 同名，并记录它引用的基础/扩展版本与 digest。

## 标准流程

1. 合并代码到 main，工作区无未提交发布内容；完成代码审查及隔离测试。
2. 创建本地 annotated tag，记录版本说明。不得仅对旧镜像重新打标签后声称它是从该 Git tag 构建。
3. 从 tag 创建干净的 detached worktree，使用正式 Dockerfile 完整构建。禁止用测试 overlay 或开发工作区代替发布源码。
4. 在构建命令里记录 Git SHA/版本标签，测试构建出来的同一镜像；失败时不推送 tag/镜像，不部署。
5. 测试通过，显式推送 main 和该 Git tag，再推送同名 Docker tag。发布失败记录当前状态，不通过移动已公开 tag 修补。
6. 校验远端 digest，记录 Git commit、Git tag、镜像地址/digest、架构、构建命令及测试结果。
7. 发布与部署分开授权。推送代码时检查 CI 是否会自动部署；未授权部署时跳过流水线或使用不部署的发布流程。

示例（IMAGE 为当前仓库的镜像名，VERSION 为待发布版本）：

```sh
git switch main
git pull --ff-only
VERSION=3.1.6-up3
SHA=$(git rev-parse HEAD)
git tag -a "$VERSION" -m "Release $VERSION" "$SHA"
git worktree add --detach ../release-build-"$VERSION" "$VERSION"
docker build --label org.opencontainers.image.revision="$SHA" \
  --label org.opencontainers.image.version="$VERSION" \
  -t "$IMAGE:$VERSION" ../release-build-"$VERSION"
# 执行本仓库对应测试，通过后再发布：
git push <发布remote> main "refs/tags/$VERSION"
docker push "$IMAGE:$VERSION"
docker buildx imagetools inspect "$IMAGE:$VERSION"
git worktree remove ../release-build-"$VERSION"
```

基础仓库发布到自己的 fork，不向上游原作者仓库推送。GitLab 可使用 `git push -o ci.skip` 避免触发流水线；这不替代发布前测试。对不同仓库分别授权，不因基础发布自动推送业务应用。

## 可重建与回滚

- 下游 FROM 必须引用已发布版本，正式发布优先固定 digest，禁止依赖本地 `root-init-test`、latest 或未发布基础。
- 保存构建日志、来源信息和旧镜像 digest。Git tag 固化的是源码，不自动固化外部依赖：浮动父镜像、apk 仓库、下载文件或编译器变化会改变重建结果。
- 要求字节级可复现时，还需固定父镜像 digest、依赖版本/校验值及归档安装源；仅有 Git tag 不能承诺重建出相同 Docker digest。
- 镜像被意外覆盖时，先使用保留的旧 digest 恢复；如需重建，从该版本 Git tag 构建并重新测试，记录新 digest，明确是恢复发布，不静默改写源码 tag。
- 回滚应用必须同时恢复旧镜像及匹配配置/端口；涉及 Cron 时先停稳旧任务再切换，防止双跑。
- 生产验收未完成、异常 Cron 规则未确认时，不因 tag 或镜像发布成功而放行部署。

## 当前历史说明

此前基础本地工作树目录名 `nginx-php-fpm-up2` 和误推的版本分支不是可靠发布标识；保留不代表继续使用版本分支流程。正式版本以核对后的 Git tag 和镜像 digest 为准。之前基于本地 up2 的 root-init overlay 仅属于隔离联调，不作为 tag 全源码发布构建记录。
