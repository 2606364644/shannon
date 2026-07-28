/** 新密码最小长度。镜像后端 auth/passwords.py::NEW_PASSWORD_MIN_LEN（=8，SSOT）。
 *  改后端阈值时同步此处——曾因前后端不一致（前端零长度校验）导致新建用户
 *  填短密码被后端静默 400，前端只弹笼统「创建失败」无从排查。 */
export const PASSWORD_MIN_LEN = 8;
