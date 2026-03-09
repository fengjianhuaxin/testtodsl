"""认证模块 - 用户登录/登出/会话管理"""
import json
import os
import hashlib
import time

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")

# 内存中的 session 存储
_sessions = {}


def _load_users():
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def login(username, password):
    """登录验证，返回 (success, user_info/error_msg, token)"""
    users = _load_users()
    for u in users:
        if u["username"] == username and u["password"] == password:
            if not u.get("can_login", True):
                return False, "账号已被禁用", None
            token = hashlib.md5(f"{username}{time.time()}".encode()).hexdigest()
            _sessions[token] = {
                "username": u["username"],
                "name": u["name"],
                "role": u["role"],
                "can_query": u.get("can_query", False),
                "is_admin": u.get("is_admin", False),
                "login_time": time.time()
            }
            return True, _sessions[token], token
    return False, "用户名或密码错误", None


def logout(token):
    _sessions.pop(token, None)


def get_current_user(token):
    """根据 token 获取当前用户，返回 None 表示未登录"""
    return _sessions.get(token)


def get_all_users():
    return _load_users()


def add_user(user_data):
    users = _load_users()
    for u in users:
        if u["username"] == user_data["username"]:
            return False, "用户名已存在"
    users.append(user_data)
    _save_users(users)
    return True, "添加成功"


def update_user(username, user_data):
    users = _load_users()
    for i, u in enumerate(users):
        if u["username"] == username:
            users[i].update(user_data)
            _save_users(users)
            return True, "更新成功"
    return False, "用户不存在"


def delete_user(username):
    users = _load_users()
    users = [u for u in users if u["username"] != username]
    _save_users(users)
    return True, "删除成功"
