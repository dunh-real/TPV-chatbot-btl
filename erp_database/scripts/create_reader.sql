-- Login chi doc cho ket noi tu xa. Mat khau lay tu bien moi truong MSSQL_RO_PASSWORD (sqlcmd scripting variable).
SET NOCOUNT ON;
IF SUSER_ID(N'tpv_reader') IS NULL
    CREATE LOGIN [tpv_reader] WITH PASSWORD = N'$(MSSQL_RO_PASSWORD)', CHECK_POLICY = ON, CHECK_EXPIRATION = OFF,
        DEFAULT_DATABASE = [bqp-private-ai-assistant];
USE [bqp-private-ai-assistant];
IF USER_ID(N'tpv_reader') IS NULL
    CREATE USER [tpv_reader] FOR LOGIN [tpv_reader];
ALTER ROLE [db_datareader] ADD MEMBER [tpv_reader];
SELECT dp.name AS user_name, r.name AS role_name FROM sys.database_role_members m
JOIN sys.database_principals dp ON dp.principal_id = m.member_principal_id
JOIN sys.database_principals r ON r.principal_id = m.role_principal_id WHERE dp.name = N'tpv_reader';
