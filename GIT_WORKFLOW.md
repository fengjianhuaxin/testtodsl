# Demo1 项目 Git 操作说明

## 仓库信息
- 远程仓库：`https://github.com/fengjianhuaxin/testtodsl.git`
- 主分支：`main`
- 本地目录：`D:\Codex\sql_create_json版_项目数据版\Demo1`

## 日常最简流程（推荐）
1. 进入项目目录
   ```powershell
   cd D:\Codex\sql_create_json版_项目数据版\Demo1
   ```
2. 看当前状态
   ```powershell
   git status -sb
   git branch -vv
   ```
3. 先拉最新代码
   ```powershell
   git checkout main
   git pull --rebase origin main
   ```
4. 开发并自测
5. 提交并推送
   ```powershell
   git add .
   git commit -m "feat: 变更说明"
   git push origin main
   ```

## 多人协作流程（更稳）
多人同时改代码时，建议用功能分支：

```powershell
git checkout main
git pull --rebase origin main
git checkout -b feat/xxx
# 开发
git add .
git commit -m "feat: xxx"
git push -u origin feat/xxx
```

然后在 GitHub 发起 PR 合并到 `main`。

## 拉取冲突处理
执行 `git pull --rebase origin main` 出现冲突时：

1. 打开冲突文件，处理 `<<<<<<<` / `=======` / `>>>>>>>`
2. 标记已解决并继续
   ```powershell
   git add <冲突文件>
   git rebase --continue
   ```
3. 如果想放弃本次 rebase
   ```powershell
   git rebase --abort
   ```

## 回滚方式
### 1）推荐：回滚某次提交（安全）
会新增一条“反向提交”，保留历史：

```powershell
git log --oneline -n 20
git revert <commit_id>
git push origin main
```

### 2）只回滚本地未提交改动
丢弃当前工作区改动：

```powershell
git restore .
```

把本地重置到最近一次提交：

```powershell
git reset --hard HEAD
```

## 常用命令速查
```powershell
git status -sb
git log --oneline --graph --decorate -n 20
git remote -v
git fetch --all --prune
```

## 提交信息建议
- `feat:` 新功能
- `fix:` 修复
- `refactor:` 重构
- `docs:` 文档
- `chore:` 维护类改动

示例：
- `feat: 增加本体关系回退匹配`
- `fix: 统一布尔值映射避免SQL猜值`
- `docs: 新增Git操作说明`

## 本项目约定
- `main` 分支保持可运行
- 推送前先拉取，减少冲突
- 小步提交，方便回滚
- 不要提交真实密钥/密码等敏感信息
