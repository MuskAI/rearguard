-- 在能使用本地管理员身份执行的前提下运行一次（例如: sudo mysql < sql/set_root_localhost_password.sql）
-- 将 root@localhost 密码设为强密码（避免 validate_password 报 ERROR 1819）
-- 与「应用账号 root1」密码不同；按需修改后再执行。

ALTER USER 'root'@'localhost' IDENTIFIED BY '<CHANGE_ME_STRONG_MYSQL_ROOT_PASSWORD>';
FLUSH PRIVILEGES;
